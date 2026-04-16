"""
Small Cap Trade Executor.

Handles the full trade lifecycle for a single position:
  1. Entry — limit buy at breakout level + offset
  2. Partial exit 1 — sell 1/3 at +10% from entry
  3. Partial exit 2 — sell another 1/3 at +20% from entry
  4. Trail stop — remaining 1/3 trails 5% below running high
  5. Halt protection — immediately cancel/close on halt detection
  6. Time stop — force close if held beyond MAX_HOLD_MINUTES
  7. EOD flatten — force close all positions at EOD_STOP_BEFORE_MIN before flatten

All orders use DAY limit orders (NORMAL session during market hours).
Uses account_hash (not account_number) for order placement per Schwab API.

Usage:
    exec = TradeExecutor(client, account_hash, risk_manager, stream_manager)
    result = exec.enter(signal, ofe_score)   # PatternSignal + OrderFlowEngine score
    exec.manage_positions()                   # called every ~1s in market loop
    exec.flatten_all()                        # called at EOD
"""

import time
import threading
from datetime import datetime, timezone
from loguru import logger

import httpx
from schwab.orders.equities import equity_buy_limit, equity_sell_limit
from schwab.orders.common import Duration, Session

from smallcap.config import (
    PARTIAL_1_TARGET_PCT,
    PARTIAL_2_TARGET_PCT,
    TRAIL_STOP_PCT,
    BREAKEVEN_TRIGGER_PCT,
    MAX_HOLD_MINUTES,
    EOD_STOP_BEFORE_MIN,
    EOD_FLATTEN,
    MIN_BREAKOUT_SCORE,
    LIMIT_ORDER_OFFSET,
    USE_LIMIT_ORDERS,
    SELL_SLIPPAGE_PCT,
    STOP_SELL_SLIPPAGE_PCT,
    QUOTE_STALENESS_SEC,
)


class TradeExecutor:
    """
    Manages entry, scaling, and exit for small cap momentum trades.
    Thread-safe — manage_positions() can run in a background thread.
    """

    def __init__(self, client, account_hash: str, risk_manager, stream_manager):
        self._client  = client
        self._acct    = account_hash
        self._risk    = risk_manager
        self._stream  = stream_manager

        # Active positions: {symbol: _PositionTracker}
        self._positions: dict[str, _PositionTracker] = {}
        self._lock = threading.Lock()

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def enter(self, signal, ofe_score: int) -> dict:
        """
        Attempt to enter a position based on a PatternSignal and OFE score.

        Pre-conditions checked here:
          - OFE score >= MIN_BREAKOUT_SCORE
          - Not already long the symbol
          - Risk manager approves

        Returns:
          {"entered": bool, "shares": int, "order_id": str | None, "reason": str}
        """
        sym = signal.symbol

        # ── OFE gate ──
        if ofe_score < MIN_BREAKOUT_SCORE:
            return _no_entry(
                f"OFE score {ofe_score} below threshold {MIN_BREAKOUT_SCORE}"
            )

        # ── Already in position ──
        with self._lock:
            if sym in self._positions:
                return _no_entry(f"already long {sym}")

        # ── Quote freshness ──
        quote = self._stream.get_quote(sym)
        if not quote:
            return _no_entry(f"no live quote for {sym}")

        # Reject stale quotes — if the last L1 update was more than
        # QUOTE_STALENESS_SEC ago the price we're acting on is too old.
        quote_ts = quote.get("ts")
        if quote_ts:
            staleness = (_utcnow() - quote_ts).total_seconds()
            if staleness > QUOTE_STALENESS_SEC:
                return _no_entry(
                    f"stale quote for {sym} ({staleness:.0f}s old, "
                    f"limit={QUOTE_STALENESS_SEC}s)"
                )

        # ── Price already past entry level? ──
        # If the stock has run more than 2× LIMIT_ORDER_OFFSET beyond the signal
        # entry, we've missed the breakout. Entering now chases the move.
        live_last = quote.get("last") or quote.get("mark") or 0
        if live_last > 0 and live_last > signal.entry + LIMIT_ORDER_OFFSET * 4:
            return _no_entry(
                f"{sym} has already moved past entry "
                f"(live=${live_last:.2f}, entry=${signal.entry:.2f}) — chasing"
            )

        # ── Halt check ──
        if self._stream.is_halted(sym):
            return _no_entry(f"{sym} is halted")

        # ── Risk manager gate ──
        decision = self._risk.check_entry(
            sym,
            entry_price=signal.entry,
            stop_price=signal.stop,
            target1=signal.target1,
            stream=self._stream,
        )
        if not decision["allowed"]:
            return _no_entry(decision["reason"])

        shares = decision["shares"]

        # ── Place order ──
        limit_price = str(round(signal.entry + LIMIT_ORDER_OFFSET, 2))
        order_id = self._place_buy(sym, shares, limit_price)

        if order_id is None:
            return _no_entry(f"order placement failed for {sym}")

        # ── Register position tracker ──
        tracker = _PositionTracker(
            symbol=sym,
            shares_total=shares,
            entry_price=signal.entry,
            stop_price=signal.stop,
            target1=signal.target1,
            target2=signal.target2,
            order_id=order_id,
        )
        with self._lock:
            self._positions[sym] = tracker

        # Notify risk manager so position tracking + P&L are accurate
        self._risk.record_fill(sym, shares=shares, fill_price=signal.entry)

        logger.info(
            f"ENTRY: {sym} | {shares} shares | "
            f"limit=${limit_price} | stop=${signal.stop:.2f} | "
            f"t1=${signal.target1:.2f} t2=${signal.target2:.2f} | "
            f"OFE={ofe_score} | {signal.pattern}"
        )

        return {
            "entered":  True,
            "shares":   shares,
            "order_id": order_id,
            "reason":   "entered",
        }

    def manage_positions(self):
        """
        Called every ~1s during market hours.
        Checks each open position for:
          - Partial exit at target1 (+10%)
          - Partial exit at target2 (+20%)
          - Trail stop on remaining shares
          - Breakeven stop move
          - Time stop (MAX_HOLD_MINUTES)
          - EOD time stop
          - Halt protection
        """
        with self._lock:
            symbols = list(self._positions.keys())

        for sym in symbols:
            try:
                self._manage_one(sym)
            except Exception as e:
                logger.warning(f"manage_positions error for {sym}: {e}")

    def flatten_all(self, reason: str = "EOD"):
        """Force close all open positions at market / limit."""
        with self._lock:
            symbols = list(self._positions.keys())

        for sym in symbols:
            with self._lock:
                tracker = self._positions.get(sym)
            if tracker and tracker.shares_remaining > 0:
                self._execute_sell(tracker, tracker.shares_remaining, reason=reason)

    def get_positions(self) -> dict:
        with self._lock:
            return {
                sym: {
                    "shares_remaining": t.shares_remaining,
                    "entry_price":      t.entry_price,
                    "stop_price":       t.stop_price,
                    "running_high":     t.running_high,
                    "partial1_done":    t.partial1_done,
                    "partial2_done":    t.partial2_done,
                }
                for sym, t in self._positions.items()
                if t.shares_remaining > 0
            }

    # ── POSITION MANAGEMENT ────────────────────────────────────────────────────

    def _manage_one(self, sym: str):
        with self._lock:
            tracker = self._positions.get(sym)
        if not tracker or tracker.shares_remaining <= 0:
            return

        quote = self._stream.get_quote(sym)
        if not quote:
            return

        last = quote.get("last") or quote.get("mark") or 0
        if last <= 0:
            return

        now = _utcnow()

        # ── Halt protection — immediate market close ──
        if self._stream.is_halted(sym):
            logger.warning(f"HALT on {sym} with open position — closing at market")
            self._execute_sell(tracker, tracker.shares_remaining, reason="halt")
            return

        # ── Time stops ──
        hold_minutes = (now - tracker.entry_time).total_seconds() / 60
        # Use local wall-clock time for CT-based session constants
        local_now = datetime.now()
        h = local_now.hour + local_now.minute / 60.0
        eod_cutoff = EOD_FLATTEN - EOD_STOP_BEFORE_MIN / 60.0

        if h >= eod_cutoff:
            self._execute_sell(tracker, tracker.shares_remaining, reason="EOD_flatten")
            return

        if hold_minutes >= MAX_HOLD_MINUTES:
            self._execute_sell(tracker, tracker.shares_remaining, reason="time_stop")
            return

        # ── Update running high ──
        if last > tracker.running_high:
            tracker.running_high = last

        # ── Breakeven stop ──
        if not tracker.breakeven_set:
            gain_pct = (last - tracker.entry_price) / tracker.entry_price
            if gain_pct >= BREAKEVEN_TRIGGER_PCT:
                tracker.stop_price = tracker.entry_price + 0.01
                tracker.breakeven_set = True
                logger.info(
                    f"{sym}: stop moved to breakeven ${tracker.stop_price:.2f} "
                    f"(price=${last:.2f}, +{gain_pct:.1%})"
                )

        # ── Partial exit 1: +10% ──
        if not tracker.partial1_done and last >= tracker.target1:
            shares_to_sell = tracker.shares_total // 3
            if shares_to_sell > 0 and shares_to_sell <= tracker.shares_remaining:
                self._execute_sell(tracker, shares_to_sell, reason="partial1")
                # After first partial, move stop to breakeven minimum
                if not tracker.breakeven_set:
                    tracker.stop_price = max(tracker.stop_price, tracker.entry_price)
                    tracker.breakeven_set = True
                tracker.partial1_done = True

        # ── Partial exit 2: +20% ──
        if not tracker.partial2_done and last >= tracker.target2:
            shares_to_sell = tracker.shares_total // 3
            if shares_to_sell > 0 and shares_to_sell <= tracker.shares_remaining:
                self._execute_sell(tracker, shares_to_sell, reason="partial2")
                tracker.partial2_done = True
                # Activate trail stop on remaining position
                tracker.trail_active = True

        # ── Trail stop (active after partial2) ──
        if tracker.trail_active and tracker.shares_remaining > 0:
            trail_stop = tracker.running_high * (1 - TRAIL_STOP_PCT)
            if trail_stop > tracker.stop_price:
                tracker.stop_price = trail_stop

        # ── Hard stop hit ──
        if last <= tracker.stop_price and tracker.shares_remaining > 0:
            logger.info(
                f"{sym}: stop hit ${tracker.stop_price:.2f} "
                f"(last=${last:.2f}) — closing {tracker.shares_remaining} shares"
            )
            self._execute_sell(tracker, tracker.shares_remaining, reason="stop_hit")

    # ── ORDER HELPERS ──────────────────────────────────────────────────────────

    def _place_buy(self, sym: str, shares: int, limit_price: float) -> str | None:
        """Place a limit buy. Returns order_id string or None on failure."""
        try:
            if USE_LIMIT_ORDERS:
                order = (
                    equity_buy_limit(sym, shares, str(limit_price))
                    .set_duration(Duration.DAY)
                    .set_session(Session.NORMAL)
                    .build()
                )
            else:
                from schwab.orders.equities import equity_buy_market
                order = (
                    equity_buy_market(sym, shares)
                    .set_duration(Duration.DAY)
                    .set_session(Session.NORMAL)
                    .build()
                )

            resp = self._client.place_order(self._acct, order)
            if resp.status_code in (200, 201):
                # Order ID is in the Location header
                location = resp.headers.get("Location", "")
                order_id = location.split("/")[-1] if "/" in location else "unknown"
                return order_id
            else:
                logger.error(
                    f"Buy order failed: {sym} {shares}@{limit_price} "
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
                return None
        except Exception as e:
            logger.error(f"Buy order exception for {sym}: {e}")
            return None

    def _execute_sell(self, tracker: "_PositionTracker", shares: int, reason: str):
        """Place a limit sell and record the close in risk manager."""
        sym   = tracker.symbol
        quote = self._stream.get_quote(sym)

        last = (quote.get("last") or quote.get("mark") or tracker.entry_price) if quote else tracker.entry_price
        bid  = quote.get("bid", 0) if quote else 0

        # For planned partial exits (locking in gains), use last × small slippage.
        # For urgent exits (stop hit, halt, time/EOD) use bid − small offset:
        #   bid is the best price buyers are currently paying right now,
        #   so bid - STOP_SELL_SLIPPAGE guarantees near-immediate fill even on
        #   a fast-moving small cap where last is stale.
        urgent = reason in ("stop_hit", "halt", "EOD_flatten", "time_stop", "EOD")
        if urgent and bid > 0:
            limit_price = round(bid * (1 - STOP_SELL_SLIPPAGE_PCT), 2)
        else:
            limit_price = round(last * (1 - SELL_SLIPPAGE_PCT), 2)

        # Final sanity: never sell below 50% of entry (protects against stale/bad quotes)
        floor_price = round(tracker.entry_price * 0.50, 2)
        limit_price = max(limit_price, floor_price)

        try:
            order = (
                equity_sell_limit(sym, shares, str(limit_price))
                .set_duration(Duration.DAY)
                .set_session(Session.NORMAL)
                .build()
            )
            resp = self._client.place_order(self._acct, order)
            if resp.status_code in (200, 201):
                logger.info(
                    f"SELL ({reason}): {sym} {shares} shares @ ${limit_price:.2f}"
                )
                self._risk.record_close(sym, limit_price, shares=shares, reason=reason)
                tracker.shares_remaining -= shares
                if tracker.shares_remaining <= 0:
                    with self._lock:
                        self._positions.pop(sym, None)
            else:
                logger.error(
                    f"Sell order failed ({reason}): {sym} {shares}@{limit_price} "
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
        except Exception as e:
            logger.error(f"Sell order exception for {sym} ({reason}): {e}")


class _PositionTracker:
    """Mutable state for a single open position."""

    __slots__ = (
        "symbol", "shares_total", "shares_remaining",
        "entry_price", "stop_price", "target1", "target2",
        "order_id", "entry_time",
        "running_high", "breakeven_set", "trail_active",
        "partial1_done", "partial2_done",
    )

    def __init__(
        self,
        symbol: str,
        shares_total: int,
        entry_price: float,
        stop_price: float,
        target1: float,
        target2: float,
        order_id: str,
    ):
        self.symbol           = symbol
        self.shares_total     = shares_total
        self.shares_remaining = shares_total
        self.entry_price      = entry_price
        self.stop_price       = stop_price
        self.order_id         = order_id
        self.entry_time       = _utcnow()
        self.running_high     = entry_price
        self.breakeven_set    = False
        self.trail_active     = False
        self.partial1_done    = False
        self.partial2_done    = False

        # ── Target levels ─────────────────────────────────────────────────────
        # Use pattern-measured targets from PatternSignal (flagpole extension,
        # AB extension, OR range extension). These are Ross Cameron's chart-based
        # exits, more precise than fixed percentages.
        # Validate: each target must represent ≥ 2:1 R:R (t1) / ≥ 3:1 R:R (t2).
        # If the signal targets don't meet the floor, fall back to fixed %.
        risk_amt = entry_price - stop_price
        floor_t1 = round(entry_price + max(risk_amt * 2.0,
                                           entry_price * PARTIAL_1_TARGET_PCT), 2)
        floor_t2 = round(entry_price + max(risk_amt * 3.0,
                                           entry_price * PARTIAL_2_TARGET_PCT), 2)

        # Use signal target if it clears the floor; otherwise use the floor
        self.target1 = target1 if (target1 is not None and target1 >= floor_t1) else floor_t1
        self.target2 = target2 if (target2 is not None and target2 > self.target1) else floor_t2


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _no_entry(reason: str) -> dict:
    logger.debug(f"Entry denied: {reason}")
    return {"entered": False, "shares": 0, "order_id": None, "reason": reason}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
