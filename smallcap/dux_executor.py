"""
Dux Trade Executor.

Manages the full trade lifecycle for Dux SHORT and LONG positions:

  SHORT lifecycle (FRD, Spike Short, H&S):
    1. Entry    — SELL SHORT limit at ask + offset
    2. Partial cover 1 — BUY_TO_COVER 50% at T1 (VWAP target)
    3. Trail stop  — remaining 50% trails 5% above the running low
    4. Hard stop   — BUY_TO_COVER all if price rises to stop level
    5. Time stop   — cover all after MAX_HOLD_MINUTES
    6. Halt guard  — immediate BUY_TO_COVER if halt detected
    7. EOD flatten — cover all before EOD cutoff

  LONG lifecycle (Dip Panic):
    1. Entry  — BUY limit at close + offset
    2. Partial sell 1 — SELL 50% at T1 (VWAP)
    3. Trail stop  — remaining 50% trails 5% below the running high
    4. Hard stop   — SELL all if price drops to stop
    5. Time/halt/EOD stops (same as SHORT)

Short-selling specifics:
  - Locate check via Schwab quote before any SELL_SHORT order
  - SELL_SHORT and BUY_TO_COVER use the generic OrderBuilder (reliable across
    all schwab-py versions, avoiding potential convenience-function name drift)
  - Paper simulation: if account_hash is None or paper=True, orders are
    simulated internally rather than sent to Schwab

Usage:
    exec = DuxExecutor(client, account_hash, dux_risk_manager, stream_manager)
    exec.enter(signal)           # DuxPatternSignal
    exec.manage_positions()      # call every ~1s in the main loop
    exec.flatten_all(reason)     # at EOD
"""

import threading
from datetime import datetime, timezone
from loguru import logger

try:
    from schwab.orders.common import (
        OrderType, Session, Duration, Instruction, OrderStrategyType,
    )
    from schwab.orders.generic import OrderBuilder
    _SCHWAB_ORDERS_AVAILABLE = True
except ImportError:
    _SCHWAB_ORDERS_AVAILABLE = False
    logger.warning("schwab.orders not importable — Dux executor will paper-simulate all orders")

import time as _time

from smallcap.dux_config import (
    DUX_PARTIAL1_FRAC,
    DUX_TRAIL_PCT,
    DUX_BREAKEVEN_TRIGGER,
    DUX_MAX_HOLD_MINUTES,
    DUX_EOD_STOP_BEFORE_MIN,
    DUX_EOD_FLATTEN_CT,
    DUX_SHORT_ENTRY_OFFSET,
    DUX_COVER_SLIPPAGE_PCT,
    DUX_STOP_COVER_SLIPPAGE,
    DUX_QUOTE_STALENESS_SEC,
    DUX_MIN_SIGNAL_STRENGTH,
    DUX_MAX_ENTRY_DRIFT_PCT,
)

# Locate cache TTL: re-check shortable status at most once every 5 minutes
_LOCATE_CACHE_TTL = 300


class DuxExecutor:
    """
    Executes and manages Dux SHORT and LONG positions.
    Thread-safe — manage_positions() can run in the main loop tick.
    """

    def __init__(self, client, account_hash: str | None,
                 risk_manager, stream_manager, paper: bool = False):
        """
        Args:
            client:       Authenticated schwab-py Client instance.
            account_hash: Schwab account hash for order placement.
                          Pass None to force paper simulation.
            risk_manager: DuxRiskManager instance.
            stream_manager: StreamManager instance (for live quotes).
            paper:        If True, simulate all orders (no real orders sent).
        """
        self._client  = client
        self._acct    = account_hash
        self._risk    = risk_manager
        self._stream  = stream_manager
        self._paper   = paper or (account_hash is None)

        # Active positions: {symbol: _DuxTracker}
        self._positions: dict[str, "_DuxTracker"] = {}
        self._lock = threading.Lock()

        # Locate cache: {symbol: (shortable: bool, checked_monotonic: float)}
        # Avoids a blocking HTTP call on every entry attempt.
        self._locate_cache: dict[str, tuple[bool, float]] = {}

        if self._paper:
            logger.info("[DuxExec] Paper simulation mode — no real orders will be sent")

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def enter(self, signal) -> dict:
        """
        Attempt to enter a position based on a DuxPatternSignal.

        Returns:
          {"entered": bool, "shares": int, "order_id": str|None, "reason": str}
        """
        sym = signal.symbol.upper()

        # ── Signal strength gate ──
        if signal.strength < DUX_MIN_SIGNAL_STRENGTH:
            return _no_entry(f"signal strength {signal.strength} < {DUX_MIN_SIGNAL_STRENGTH}")

        # ── Already in this position ──
        with self._lock:
            if sym in self._positions:
                return _no_entry(f"already have a Dux position in {sym}")

        # ── Quote freshness ──
        quote = self._stream.get_quote(sym)
        if not quote:
            return _no_entry(f"no live quote for {sym}")

        quote_ts = quote.get("ts")
        if quote_ts:
            staleness = (_utcnow() - quote_ts).total_seconds()
            if staleness > DUX_QUOTE_STALENESS_SEC:
                return _no_entry(
                    f"stale quote for {sym} "
                    f"({staleness:.0f}s old, limit={DUX_QUOTE_STALENESS_SEC}s)"
                )

        # ── Halt check ──
        if self._stream.is_halted(sym):
            return _no_entry(f"{sym} is currently halted")

        # ── Locate check for shorts (cached — avoids blocking the main loop) ──
        if signal.direction == "SHORT" and not self._paper:
            if not self._get_locate(sym):
                return _no_entry(f"{sym} not shortable / hard-to-borrow")

        # ── Risk manager gate ──
        decision = self._risk.check_entry(
            symbol=sym,
            entry_price=signal.entry,
            stop_price=signal.stop,
            target1=signal.target1,
            direction=signal.direction,
            strength=signal.strength,
        )
        if not decision["allowed"]:
            return _no_entry(decision["reason"])

        shares     = decision["shares"]
        dollar_risk = decision["dollar_risk"]

        # ── Determine entry limit price ──
        ask  = quote.get("ask") or quote.get("last") or signal.entry
        bid  = quote.get("bid") or quote.get("last") or signal.entry
        last = quote.get("last") or quote.get("mark") or signal.entry

        # ── Signal price drift gate ──────────────────────────────────────────
        # Signals are computed up to DUX_SIGNAL_EXPIRY_MIN minutes before this
        # runs.  If price has moved too far from signal.entry, the original
        # stop/size math is stale and the entry is no longer clean.
        drift = abs(last - signal.entry) / signal.entry if signal.entry > 0 else 1.0
        if drift > DUX_MAX_ENTRY_DRIFT_PCT:
            return _no_entry(
                f"{sym} price ${last:.2f} has drifted {drift:.1%} from signal entry "
                f"${signal.entry:.2f} (limit {DUX_MAX_ENTRY_DRIFT_PCT:.0%}) — skipping"
            )

        if signal.direction == "SHORT":
            # SELL SHORT limit = minimum acceptable sale price.
            # Setting at (bid - offset) guarantees an immediate fill: we accept
            # the current bid minus a small buffer to absorb spread noise.
            limit_price = round(bid - DUX_SHORT_ENTRY_OFFSET, 2)
            if limit_price <= 0:
                return _no_entry(f"{sym} computed short limit price <= 0")
        else:
            # LONG (Dip Panic): buy just above current ask
            limit_price = round(ask + 0.02, 2)

        # ── Place order ──
        order_id = self._place_order(sym, shares, limit_price, signal.direction)

        if order_id is None and not self._paper:
            return _no_entry(f"order placement failed for {sym}")

        # ── Register position tracker ──
        fill_price = limit_price   # assume immediate fill at limit
        tracker = _DuxTracker(
            symbol=sym,
            shares_total=shares,
            entry_price=fill_price,
            stop_price=signal.stop,
            target1=signal.target1,
            target2=signal.target2,
            direction=signal.direction,
            expected_risk=dollar_risk,
        )
        with self._lock:
            self._positions[sym] = tracker

        self._risk.record_fill(
            symbol=sym,
            shares=shares,
            fill_price=fill_price,
            direction=signal.direction,
            expected_risk=dollar_risk,
        )

        logger.info(
            f"[DuxExec] ENTRY: {sym} {signal.direction} {shares} shares | "
            f"limit=${limit_price:.2f} stop=${signal.stop:.2f} | "
            f"t1=${signal.target1:.2f} t2=${signal.target2:.2f} | "
            f"pattern={signal.pattern} str={signal.strength} "
            f"{'[PAPER]' if self._paper else ''}"
        )

        return {
            "entered":  True,
            "shares":   shares,
            "order_id": order_id or "PAPER",
            "reason":   "entered",
        }

    def manage_positions(self):
        """
        Called every ~1s during market hours.
        Checks each open position for stop, target, trail, time, and halt exits.
        """
        with self._lock:
            symbols = list(self._positions.keys())

        for sym in symbols:
            try:
                self._manage_one(sym)
            except Exception as e:
                logger.warning(f"[DuxExec] manage_positions error for {sym}: {e}")

    def flatten_all(self, reason: str = "EOD"):
        """Force close all open positions."""
        with self._lock:
            symbols = list(self._positions.keys())
        for sym in symbols:
            with self._lock:
                tracker = self._positions.get(sym)
            if tracker and tracker.shares_remaining > 0:
                self._execute_exit(tracker, tracker.shares_remaining, reason=reason)

    def get_open_positions(self) -> dict:
        """Return a snapshot of all open Dux positions."""
        with self._lock:
            return {
                sym: {
                    "shares_remaining": t.shares_remaining,
                    "entry_price":      t.entry_price,
                    "stop_price":       t.stop_price,
                    "direction":        t.direction,
                    "running_extreme":  t.running_extreme,
                    "partial1_done":    t.partial1_done,
                }
                for sym, t in self._positions.items()
                if t.shares_remaining > 0
            }

    # ── POSITION MANAGEMENT ───────────────────────────────────────────────────

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

        # ── Halt guard — immediate cover/sell ──────────────────────────────
        if self._stream.is_halted(sym):
            logger.warning(
                f"[DuxExec] HALT on {sym} with Dux position "
                f"({tracker.direction}) — closing immediately"
            )
            self._execute_exit(tracker, tracker.shares_remaining, reason="halt")
            return

        # ── Time stops ────────────────────────────────────────────────────
        hold_minutes = (now - tracker.entry_time).total_seconds() / 60
        local_now    = datetime.now()
        h_ct         = local_now.hour + local_now.minute / 60.0
        eod_cutoff   = DUX_EOD_FLATTEN_CT - DUX_EOD_STOP_BEFORE_MIN / 60.0

        if h_ct >= eod_cutoff:
            self._execute_exit(tracker, tracker.shares_remaining, reason="EOD_flatten")
            return

        if hold_minutes >= DUX_MAX_HOLD_MINUTES:
            self._execute_exit(tracker, tracker.shares_remaining, reason="time_stop")
            return

        # ── Direction-aware position management ───────────────────────────
        if tracker.direction == "SHORT":
            self._manage_short(tracker, last)
        else:
            self._manage_long(tracker, last)

    def _manage_short(self, tracker: "_DuxTracker", last: float):
        """Manage an open short position."""
        sym = tracker.symbol

        # Update running low (tracks lowest price hit — our "profit" direction)
        if last < tracker.running_extreme:
            tracker.running_extreme = last

        # ── Breakeven stop: once 5% profit, never let it return to loss ──
        if not tracker.breakeven_set:
            gain_pct = (tracker.entry_price - last) / tracker.entry_price
            if gain_pct >= DUX_BREAKEVEN_TRIGGER:
                tracker.stop_price = tracker.entry_price - 0.01
                tracker.breakeven_set = True
                logger.info(
                    f"[DuxExec] {sym} SHORT: stop moved to breakeven "
                    f"${tracker.stop_price:.2f} (last=${last:.2f} +{gain_pct:.1%})"
                )

        # ── Partial cover at T1 ────────────────────────────────────────────
        if not tracker.partial1_done and last <= tracker.target1:
            shares_to_cover = max(1, int(tracker.shares_total * DUX_PARTIAL1_FRAC))
            if shares_to_cover <= tracker.shares_remaining:
                self._execute_exit(tracker, shares_to_cover, reason="partial1")
                if not tracker.breakeven_set:
                    tracker.stop_price = tracker.entry_price - 0.01
                    tracker.breakeven_set = True
                tracker.partial1_done = True
                tracker.trail_active  = True

        # ── Trailing stop (active after partial1) ─────────────────────────
        # For a short: trail_stop = running_low × (1 + TRAIL_PCT)
        # i.e. cover if price bounces trail_pct above the lowest point hit
        if tracker.trail_active and tracker.shares_remaining > 0:
            trail_stop = tracker.running_extreme * (1 + DUX_TRAIL_PCT)
            if trail_stop < tracker.stop_price:
                tracker.stop_price = trail_stop

        # ── Hard stop: price rose above stop ──────────────────────────────
        if last >= tracker.stop_price and tracker.shares_remaining > 0:
            logger.info(
                f"[DuxExec] {sym} SHORT: stop hit ${tracker.stop_price:.2f} "
                f"(last=${last:.2f}) — covering {tracker.shares_remaining} shares"
            )
            self._execute_exit(tracker, tracker.shares_remaining, reason="stop_hit")

    def _manage_long(self, tracker: "_DuxTracker", last: float):
        """Manage an open long position (Dip Panic Buy)."""
        sym = tracker.symbol

        # Update running high
        if last > tracker.running_extreme:
            tracker.running_extreme = last

        # ── Breakeven stop ────────────────────────────────────────────────
        if not tracker.breakeven_set:
            gain_pct = (last - tracker.entry_price) / tracker.entry_price
            if gain_pct >= DUX_BREAKEVEN_TRIGGER:
                tracker.stop_price = tracker.entry_price + 0.01
                tracker.breakeven_set = True
                logger.info(
                    f"[DuxExec] {sym} LONG: stop moved to breakeven "
                    f"${tracker.stop_price:.2f} (last=${last:.2f} +{gain_pct:.1%})"
                )

        # ── Partial sell at T1 ────────────────────────────────────────────
        if not tracker.partial1_done and last >= tracker.target1:
            shares_to_sell = max(1, int(tracker.shares_total * DUX_PARTIAL1_FRAC))
            if shares_to_sell <= tracker.shares_remaining:
                self._execute_exit(tracker, shares_to_sell, reason="partial1")
                if not tracker.breakeven_set:
                    tracker.stop_price = tracker.entry_price + 0.01
                    tracker.breakeven_set = True
                tracker.partial1_done = True
                tracker.trail_active  = True

        # ── Trailing stop ─────────────────────────────────────────────────
        if tracker.trail_active and tracker.shares_remaining > 0:
            trail_stop = tracker.running_extreme * (1 - DUX_TRAIL_PCT)
            if trail_stop > tracker.stop_price:
                tracker.stop_price = trail_stop

        # ── Hard stop: price dropped below stop ───────────────────────────
        if last <= tracker.stop_price and tracker.shares_remaining > 0:
            logger.info(
                f"[DuxExec] {sym} LONG: stop hit ${tracker.stop_price:.2f} "
                f"(last=${last:.2f}) — selling {tracker.shares_remaining} shares"
            )
            self._execute_exit(tracker, tracker.shares_remaining, reason="stop_hit")

    # ── ORDER HELPERS ─────────────────────────────────────────────────────────

    def _get_locate(self, symbol: str) -> bool:
        """
        Check shortable status with a TTL cache to avoid blocking the main loop.

        The first check per symbol (or after cache expiry) makes a blocking HTTP
        call.  Subsequent checks within _LOCATE_CACHE_TTL seconds return the
        cached result instantly.
        """
        now = _time.monotonic()
        cached = self._locate_cache.get(symbol)
        if cached is not None:
            shortable, checked_at = cached
            if (now - checked_at) < _LOCATE_CACHE_TTL:
                return shortable
        result = self._check_locate(symbol)
        self._locate_cache[symbol] = (result, now)
        return result

    def _check_locate(self, symbol: str) -> bool:
        """
        Ask Schwab whether a symbol is currently shortable.
        Returns True if shortable and NOT hard-to-borrow.
        Returns True in paper mode (no real borrow needed).
        """
        if self._paper:
            return True
        try:
            resp = self._client.get_quote(symbol)
            if resp.status_code != 200:
                return False
            data = resp.json().get(symbol, {})
            # Schwab quote structure varies; try both known key paths
            quote_obj = data.get("quote", data)
            shortable     = quote_obj.get("shortable", True)
            hard_to_borrow = quote_obj.get("hardToBorrow", False)
            if not shortable or hard_to_borrow:
                logger.info(
                    f"[DuxExec] Locate failed: {symbol} "
                    f"shortable={shortable} hardToBorrow={hard_to_borrow}"
                )
                return False
            return True
        except Exception as e:
            logger.warning(f"[DuxExec] Locate check error for {symbol}: {e}")
            # Fail open in paper mode, fail closed in live mode
            return self._paper

    def _place_order(
        self,
        symbol:     str,
        shares:     int,
        limit_price: float,
        direction:  str,
    ) -> str | None:
        """
        Place a limit order. Returns order_id string, "PAPER" for simulated,
        or None on failure.

        direction: "SHORT" → SELL_SHORT
                   "LONG"  → BUY (Dip Panic long entry)
        """
        if self._paper:
            logger.info(
                f"[DuxExec][PAPER] {'SELL_SHORT' if direction == 'SHORT' else 'BUY'} "
                f"{symbol} {shares} @ ${limit_price:.2f}"
            )
            return "PAPER"

        if not _SCHWAB_ORDERS_AVAILABLE:
            logger.error("[DuxExec] schwab.orders not available — cannot place real orders")
            return None

        try:
            instruction = (
                Instruction.SELL_SHORT if direction == "SHORT" else Instruction.BUY
            )
            order = (
                OrderBuilder()
                .set_order_type(OrderType.LIMIT)
                .set_price(round(limit_price, 2))
                .set_session(Session.NORMAL)
                .set_duration(Duration.DAY)
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_equity_leg(instruction, symbol, shares)
                .build()
            )
            resp = self._client.place_order(self._acct, order)
            if resp.status_code in (200, 201):
                location = resp.headers.get("Location", "")
                return location.split("/")[-1] if "/" in location else "unknown"
            else:
                logger.error(
                    f"[DuxExec] Order failed: {symbol} {direction} "
                    f"{shares}@{limit_price} HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
                return None
        except Exception as e:
            logger.error(f"[DuxExec] Order exception for {symbol} ({direction}): {e}")
            return None

    def _execute_exit(self, tracker: "_DuxTracker", shares: int, reason: str):
        """
        Place a cover/sell order and record the close in the risk manager.
        """
        sym   = tracker.symbol
        quote = self._stream.get_quote(sym)
        last  = (quote.get("last") or quote.get("mark") or tracker.entry_price) if quote else tracker.entry_price
        bid   = quote.get("bid", 0) if quote else 0
        ask   = quote.get("ask", 0) if quote else 0

        urgent = reason in ("stop_hit", "halt", "EOD_flatten", "time_stop", "EOD")

        if tracker.direction == "SHORT":
            # Covering a short means BUYING back shares.
            # Urgent (stop/halt/EOD): pay above ask to guarantee immediate fill.
            # Planned (partial at T1): price is already at our profit target,
            #   so cover at last + small pct to get filled without chasing.
            if urgent and ask > 0:
                limit_price = round(ask * (1 + DUX_STOP_COVER_SLIPPAGE), 2)
            else:
                limit_price = round(last * (1 + DUX_COVER_SLIPPAGE_PCT), 2)
            # Safety floor: never cover at more than 2× entry (protection against bad quotes)
            limit_price = min(limit_price, round(tracker.entry_price * 2.0, 2))
            instruction = "BUY_TO_COVER"
        else:
            # Selling a long (Dip Panic exit)
            if urgent and bid > 0:
                limit_price = round(bid * (1 - DUX_STOP_COVER_SLIPPAGE), 2)
            else:
                limit_price = round(last * (1 - DUX_COVER_SLIPPAGE_PCT), 2)
            # Safety floor: never sell below 50% of entry
            limit_price = max(limit_price, round(tracker.entry_price * 0.50, 2))
            instruction = "SELL"

        order_id = self._place_exit_order(sym, shares, limit_price, instruction)

        if order_id is not None or self._paper:
            log_tag = "[PAPER]" if self._paper else ""
            logger.info(
                f"[DuxExec]{log_tag} EXIT ({reason}): {sym} {instruction} "
                f"{shares} shares @ ${limit_price:.2f} "
                f"(entry=${tracker.entry_price:.2f})"
            )
            self._risk.record_close(
                symbol=sym,
                close_price=limit_price,
                shares=shares,
                reason=reason,
                expected_risk=tracker.expected_risk,
            )
            tracker.shares_remaining -= shares
            if tracker.shares_remaining <= 0:
                with self._lock:
                    self._positions.pop(sym, None)
        else:
            logger.error(
                f"[DuxExec] Exit order failed for {sym} ({reason}) — position NOT closed"
            )

    def _place_exit_order(
        self,
        symbol:      str,
        shares:      int,
        limit_price: float,
        instruction: str,      # "BUY_TO_COVER" | "SELL"
    ) -> str | None:
        """Place a BUY_TO_COVER or SELL limit order. Returns order_id or None."""
        if self._paper:
            return "PAPER"

        if not _SCHWAB_ORDERS_AVAILABLE:
            return None

        try:
            instr = (
                Instruction.BUY_TO_COVER if instruction == "BUY_TO_COVER"
                else Instruction.SELL
            )
            order = (
                OrderBuilder()
                .set_order_type(OrderType.LIMIT)
                .set_price(round(limit_price, 2))
                .set_session(Session.NORMAL)
                .set_duration(Duration.DAY)
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_equity_leg(instr, symbol, shares)
                .build()
            )
            resp = self._client.place_order(self._acct, order)
            if resp.status_code in (200, 201):
                location = resp.headers.get("Location", "")
                return location.split("/")[-1] if "/" in location else "unknown"
            else:
                logger.error(
                    f"[DuxExec] Exit order failed: {symbol} {instruction} "
                    f"{shares}@{limit_price} HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
                return None
        except Exception as e:
            logger.error(f"[DuxExec] Exit order exception for {symbol}: {e}")
            return None


# ── POSITION TRACKER ──────────────────────────────────────────────────────────

class _DuxTracker:
    """Mutable state for a single open Dux position (SHORT or LONG)."""

    __slots__ = (
        "symbol", "direction", "shares_total", "shares_remaining",
        "entry_price", "stop_price", "target1", "target2",
        "expected_risk", "entry_time",
        "running_extreme",   # running low for SHORT, running high for LONG
        "breakeven_set", "trail_active", "partial1_done",
    )

    def __init__(
        self,
        symbol:        str,
        shares_total:  int,
        entry_price:   float,
        stop_price:    float,
        target1:       float,
        target2:       float,
        direction:     str,
        expected_risk: float = 0.0,
    ):
        self.symbol           = symbol
        self.direction        = direction
        self.shares_total     = shares_total
        self.shares_remaining = shares_total
        self.entry_price      = entry_price
        self.stop_price       = stop_price
        self.target1          = target1
        self.target2          = target2
        self.expected_risk    = expected_risk
        self.entry_time       = _utcnow()
        # running_extreme: tracks the best price reached in profit direction
        # SHORT → lowest price hit (starts at entry; goes DOWN = better)
        # LONG  → highest price hit (starts at entry; goes UP = better)
        self.running_extreme  = entry_price
        self.breakeven_set    = False
        self.trail_active     = False
        self.partial1_done    = False


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _no_entry(reason: str) -> dict:
    logger.debug(f"[DuxExec] Entry denied: {reason}")
    return {"entered": False, "shares": 0, "order_id": None, "reason": reason}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
