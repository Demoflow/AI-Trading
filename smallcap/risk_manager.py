"""
Small Cap Risk Manager.

Enforces all Ross Cameron risk rules before any trade is placed:

  Rule 1 — Per-trade risk cap ($250 max, 1% of $25k).
    Position size = MAX_RISK_PER_TRADE / (entry - stop).
    Never size a trade to risk more than $250.

  Rule 2 — Daily loss limit (-$500 = stop for the day).
    Once daily P&L hits -$500, deny ALL new entries for the rest of the session.

  Rule 3 — 3-strike consecutive loss circuit breaker.
    After 3 consecutive losses (regardless of P&L), stop trading for the day.
    A winning trade resets the streak counter.

  Rule 4 — Minimum 2:1 reward-to-risk.
    Reject any trade where target1 / risk < MIN_REWARD_RISK.

  Rule 5 — No averaging down.
    If already long a symbol, no additional buys below the original entry.

  Rule 6 — Max position value ($5,000 = 20% of account).
    Hard cap on dollar exposure per position regardless of sizing formula.

  Rule 7 — Halt check.
    Never enter a stock that is currently halted.

Usage:
    rm = SmallCapRiskManager()
    decision = rm.check_entry(symbol, entry_price, stop_price, target1, stream_manager)
    # decision: {"allowed": bool, "shares": int, "reason": str}

    rm.record_fill(symbol, shares, fill_price)   # after confirmed fill
    rm.record_close(symbol, close_price)          # after position closed
    rm.get_daily_pnl()                            # current session P&L
"""

import json
import os
import threading
from datetime import date, datetime
from loguru import logger

from smallcap.config import (
    STARTING_EQUITY,
    MAX_RISK_PER_TRADE,
    MAX_DAILY_LOSS,
    MAX_CONSECUTIVE_LOSSES,
    MIN_REWARD_RISK,
    ALLOW_AVERAGE_DOWN,
    MAX_POSITION_VALUE,
    MAX_SHARES_CAP,
    MAX_SIMULTANEOUS_POSITIONS,
    PORTFOLIO_PATH,
    BUY_SLIPPAGE_PCT,
)


class SmallCapRiskManager:
    """
    Stateful risk gatekeeper. Thread-safe.
    Persists session state (positions, P&L) to PORTFOLIO_PATH.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Session state (reset each day)
        self._session_date:     date  = date.today()
        self._daily_pnl:        float = 0.0
        self._consecutive_loss: int   = 0
        self._trades_today:     int   = 0
        self._daily_halted:     bool  = False

        # Open positions: {symbol: {"shares": int, "avg_price": float, "entry": float}}
        self._positions: dict[str, dict] = {}

        # Per-trade cumulative PnL: ensures streak is only updated on full close.
        # Prevents partial exits from resetting the consecutive-loss counter on what
        # is actually a net-losing trade.
        self._position_pnl: dict[str, float] = {}

        # Closed trade log for today — used by the dashboard
        self._closed_trades: list[dict] = []

        # Load persisted state (picks up same-day positions across restarts)
        self._load_state()

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def check_entry(
        self,
        symbol:       str,
        entry_price:  float,
        stop_price:   float,
        target1:      float,
        stream=None,  # StreamManager — for halt check
    ) -> dict:
        """
        Evaluate whether a new entry is allowed.

        Returns:
            {"allowed": bool, "shares": int, "dollar_risk": float, "reason": str}
        """
        sym = symbol.upper()
        self._reset_if_new_day()

        with self._lock:
            # ── Rule 2: Daily loss limit ──
            if self._daily_halted:
                return _deny(f"daily loss limit hit (P&L=${self._daily_pnl:+.2f})")

            if self._daily_pnl <= -MAX_DAILY_LOSS:
                self._daily_halted = True
                logger.warning(
                    f"DAILY LOSS LIMIT HIT — no more trades today "
                    f"(P&L=${self._daily_pnl:+.2f})"
                )
                return _deny(f"daily loss limit hit (P&L=${self._daily_pnl:+.2f})")

            # ── Rule 3: Consecutive loss circuit breaker ──
            if self._consecutive_loss >= MAX_CONSECUTIVE_LOSSES:
                return _deny(
                    f"3-strike circuit breaker ({self._consecutive_loss} consecutive losses)"
                )

            # ── Rule 8: Max simultaneous positions ──
            open_count = len(self._positions)
            if open_count >= MAX_SIMULTANEOUS_POSITIONS:
                return _deny(
                    f"max simultaneous positions ({MAX_SIMULTANEOUS_POSITIONS}) reached "
                    f"— {open_count} open"
                )

            # ── Rule 7: Halt check ──
            if stream and stream.is_halted(sym):
                return _deny(f"{sym} is currently halted")

            # ── Rule 5: No averaging down ──
            if sym in self._positions:
                pos = self._positions[sym]
                if not ALLOW_AVERAGE_DOWN and entry_price < pos["avg_price"]:
                    return _deny(
                        f"averaging down not allowed "
                        f"(current avg=${pos['avg_price']:.2f}, "
                        f"new entry=${entry_price:.2f})"
                    )

            # ── Risk / position sizing ──
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                return _deny(f"stop ${stop_price:.2f} must be below entry ${entry_price:.2f}")

            shares = int(MAX_RISK_PER_TRADE / risk_per_share)
            shares = max(1, shares)   # at least 1 share

            # Apply slippage to effective entry
            eff_entry = entry_price * (1 + BUY_SLIPPAGE_PCT)
            dollar_risk = shares * risk_per_share

            # ── Rule 6: Max position value ──
            position_value = shares * eff_entry
            if position_value > MAX_POSITION_VALUE:
                shares = int(MAX_POSITION_VALUE / eff_entry)
                position_value = shares * eff_entry
                dollar_risk = shares * risk_per_share

            # ── Hard share cap ──
            if shares > MAX_SHARES_CAP:
                shares = MAX_SHARES_CAP
                dollar_risk = shares * risk_per_share

            if shares <= 0:
                return _deny("position size computed to 0 shares")

            # ── Rule 1: Verify dollar risk is within limit ──
            if dollar_risk > MAX_RISK_PER_TRADE * 1.1:   # 10% tolerance for slippage
                return _deny(
                    f"dollar risk ${dollar_risk:.2f} exceeds limit ${MAX_RISK_PER_TRADE:.2f}"
                )

            # ── Rule 4: Minimum 2:1 R:R ──
            reward = target1 - entry_price
            rr = reward / risk_per_share if risk_per_share > 0 else 0
            if rr < MIN_REWARD_RISK:
                return _deny(
                    f"R:R {rr:.2f} below minimum {MIN_REWARD_RISK:.1f} "
                    f"(entry=${entry_price:.2f} stop=${stop_price:.2f} t1=${target1:.2f})"
                )

            logger.info(
                f"Risk check APPROVED: {sym} | "
                f"{shares} shares @ ${entry_price:.2f} | "
                f"risk=${dollar_risk:.2f} | "
                f"R:R={rr:.1f}:1 | "
                f"stop=${stop_price:.2f} t1=${target1:.2f}"
            )

            return {
                "allowed":      True,
                "shares":       shares,
                "dollar_risk":  round(dollar_risk, 2),
                "rr":           round(rr, 2),
                "reason":       "approved",
            }

    def record_fill(self, symbol: str, shares: int, fill_price: float):
        """Record a confirmed buy fill."""
        sym = symbol.upper()
        with self._lock:
            if sym in self._positions:
                # Average in (shouldn't happen if averaging-down rule is on, but handle it)
                pos = self._positions[sym]
                total_shares = pos["shares"] + shares
                avg = (pos["shares"] * pos["avg_price"] + shares * fill_price) / total_shares
                pos["shares"]    = total_shares
                pos["avg_price"] = avg
            else:
                self._positions[sym] = {
                    "shares":    shares,
                    "avg_price": fill_price,
                    "entry":     fill_price,
                }
            self._trades_today += 1
        self._save_state()
        logger.info(f"Position opened: {sym} | {shares} shares @ ${fill_price:.2f}")

    def record_close(self, symbol: str, close_price: float, shares: int | None = None, reason: str = "close"):
        """
        Record a position close (full or partial).
        Updates daily P&L and consecutive loss counter.
        """
        sym = symbol.upper()
        with self._lock:
            pos = self._positions.get(sym)
            if not pos:
                logger.warning(f"record_close: no position found for {sym}")
                return

            closed_shares = shares if shares else pos["shares"]
            pnl = (close_price - pos["avg_price"]) * closed_shares

            self._daily_pnl += pnl

            # Accumulate per-trade PnL so win/loss is only classified on full close
            self._position_pnl[sym] = self._position_pnl.get(sym, 0.0) + pnl

            # Record in today's closed-trade log (read by dashboard)
            self._closed_trades.append({
                "symbol":      sym,
                "shares":      closed_shares,
                "entry_price": round(pos["entry"], 4),
                "exit_price":  round(close_price, 4),
                "pnl":         round(pnl, 2),
                "reason":      reason,
                "time":        datetime.now().isoformat(),
            })

            # Determine if this is a full close before modifying the position
            position_fully_closed = closed_shares >= pos["shares"]

            if position_fully_closed:
                del self._positions[sym]
            else:
                pos["shares"] -= closed_shares

            # Only update streak on FULL close — prevents partial exit profits from
            # resetting a losing streak on what may be a net-negative trade.
            if position_fully_closed:
                total_trade_pnl = self._position_pnl.pop(sym, pnl)
                if total_trade_pnl < 0:
                    self._consecutive_loss += 1
                    logger.info(
                        f"Loss recorded: {sym} | trade P&L=${total_trade_pnl:+.2f} | "
                        f"consecutive losses={self._consecutive_loss}"
                    )
                else:
                    self._consecutive_loss = 0
                    logger.info(
                        f"Win recorded: {sym} | trade P&L=${total_trade_pnl:+.2f} | "
                        f"streak reset"
                    )
            else:
                logger.info(
                    f"Partial close: {sym} {closed_shares} shares "
                    f"@ ${close_price:.2f} | partial P&L=${pnl:+.2f} ({reason})"
                )

            if self._daily_pnl <= -MAX_DAILY_LOSS:
                self._daily_halted = True
                logger.warning(
                    f"DAILY LOSS LIMIT REACHED: "
                    f"P&L=${self._daily_pnl:+.2f} — session halted"
                )

        self._save_state()

    def get_daily_pnl(self) -> float:
        with self._lock:
            return self._daily_pnl

    def get_positions(self) -> dict:
        with self._lock:
            return dict(self._positions)

    def get_status(self) -> dict:
        with self._lock:
            return {
                "date":             self._session_date.isoformat(),
                "daily_pnl":        round(self._daily_pnl, 2),
                "consecutive_loss": self._consecutive_loss,
                "trades_today":     self._trades_today,
                "daily_halted":     self._daily_halted,
                "open_positions":   len(self._positions),
                "closed_trades":    list(self._closed_trades),
            }

    # ── PERSISTENCE ───────────────────────────────────────────────────────────

    def _reset_if_new_day(self):
        today = date.today()
        with self._lock:
            if today != self._session_date:
                self._session_date     = today
                self._daily_pnl        = 0.0
                self._consecutive_loss = 0
                self._trades_today     = 0
                self._daily_halted     = False
                self._positions        = {}
                self._closed_trades    = []
                self._position_pnl     = {}
                logger.info("Risk manager: new trading day — counters reset")
        self._save_state()

    def _save_state(self):
        os.makedirs(os.path.dirname(PORTFOLIO_PATH), exist_ok=True)
        with self._lock:
            state = {
                "date":             self._session_date.isoformat(),
                "daily_pnl":        self._daily_pnl,
                "consecutive_loss": self._consecutive_loss,
                "trades_today":     self._trades_today,
                "daily_halted":     self._daily_halted,
                "positions":        self._positions,
                "closed_trades":    self._closed_trades,
            }
        try:
            with open(PORTFOLIO_PATH, "w") as f:
                json.dump(state, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not save portfolio state: {e}")

    def _load_state(self):
        if not os.path.exists(PORTFOLIO_PATH):
            return
        try:
            with open(PORTFOLIO_PATH) as f:
                state = json.load(f)
            saved_date = date.fromisoformat(state.get("date", "2000-01-01"))
            if saved_date != date.today():
                return   # stale — start fresh
            with self._lock:
                self._session_date     = saved_date
                self._daily_pnl        = state.get("daily_pnl", 0.0)
                self._consecutive_loss = state.get("consecutive_loss", 0)
                self._trades_today     = state.get("trades_today", 0)
                self._daily_halted     = state.get("daily_halted", False)
                self._positions        = state.get("positions", {})
                self._closed_trades    = state.get("closed_trades", [])
            logger.info(
                f"Risk manager: loaded today's state | "
                f"P&L=${self._daily_pnl:+.2f} "
                f"trades={self._trades_today} "
                f"positions={len(self._positions)}"
            )
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"Could not load portfolio state: {e}")


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _deny(reason: str) -> dict:
    logger.info(f"Risk check DENIED: {reason}")
    return {"allowed": False, "shares": 0, "dollar_risk": 0.0, "reason": reason}
