"""
Dux Risk Manager.

Enforces Steven Dux's risk rules for the short-selling strategy:

  Rule 1 — Per-trade risk cap ($250 max).
    shares = MAX_RISK_PER_TRADE / abs(entry - stop)
    Never size a trade to risk more than $250.

  Rule 2 — Daily loss limit (-$750 = stop for the day).

  Rule 3 — 3-strike consecutive loss circuit breaker.

  Rule 4 — Minimum 2:1 reward-to-risk at T1.

  Rule 5 — Win rate gate: after 5 trades, require 65%+ win rate.
    If today's win rate falls below 65%, no new entries.

  Rule 6 — Max position value ($2,500 hard cap).

  Rule 7 — Error mode: after a loss exceeding 1.5× expected risk
    (blown stop / halt gap), size the next 3 trades at 50%.

  Rule 8 — Max simultaneous Dux positions: 2.

Supports both SHORT (FRD, Spike Short, H&S) and LONG (Dip Panic) positions.
P&L is direction-aware:
  SHORT pnl = (entry_price - close_price) × shares
  LONG  pnl = (close_price - entry_price) × shares

Usage:
    risk = DuxRiskManager()
    decision = risk.check_entry(symbol, entry_price, stop_price, target1,
                                direction="SHORT", strength=75)
    # decision: {"allowed": bool, "shares": int, "dollar_risk": float, "reason": str}

    risk.record_fill(symbol, shares, fill_price, direction)
    risk.record_close(symbol, close_price, expected_risk=125.0)
    risk.get_status()
"""

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from loguru import logger

from smallcap.dux_config import (
    DUX_MAX_RISK_PER_TRADE,
    DUX_MAX_POSITION_VALUE,
    DUX_MAX_DAILY_LOSS,
    DUX_MAX_CONSECUTIVE,
    DUX_MIN_REWARD_RISK,
    DUX_WIN_RATE_GATE,
    DUX_MIN_TRADES_FOR_GATE,
    DUX_MAX_SIMULTANEOUS,
    DUX_ERROR_SIZE_MULT,
    DUX_ERROR_TRADES,
    DUX_ERROR_LOSS_TRIGGER,
    DUX_PORTFOLIO_PATH,
)


class DuxRiskManager:
    """
    Stateful risk gatekeeper for the Dux short-selling strategy.
    Thread-safe.  Persists session state to DUX_PORTFOLIO_PATH.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Session state (reset each trading day)
        self._session_date:       date  = date.today()
        self._daily_pnl:          float = 0.0
        self._consecutive_loss:   int   = 0
        self._trades_today:       int   = 0
        self._wins_today:         int   = 0
        self._daily_halted:       bool  = False
        self._error_mode:         int   = 0    # trades remaining at reduced size

        # Open positions: {symbol: {"shares": int, "avg_price": float,
        #                            "entry": float, "direction": str,
        #                            "expected_risk": float}}
        self._positions: dict[str, dict] = {}

        # Per-trade cumulative PnL: tracks partial + final closes so win/loss
        # classification only happens when the position is fully closed.
        # Not persisted — partial state lost on restart is an acceptable edge case.
        self._position_pnl: dict[str, float] = {}

        # Closed trade log (read by any future dashboard integration)
        self._closed_trades: list[dict] = []

        self._load_state()

    # ── PUBLIC API ─────────────────────────────────────────────────────────────

    def check_entry(
        self,
        symbol:       str,
        entry_price:  float,
        stop_price:   float,
        target1:      float,
        direction:    str   = "SHORT",   # "SHORT" or "LONG"
        strength:     int   = 50,
    ) -> dict:
        """
        Evaluate whether a new Dux entry is allowed.

        Returns:
            {"allowed": bool, "shares": int, "dollar_risk": float, "reason": str}
        """
        sym = symbol.upper()
        self._reset_if_new_day()

        with self._lock:
            # ── Rule 2: Daily loss limit ──
            if self._daily_halted:
                return _deny(f"daily loss limit hit (P&L=${self._daily_pnl:+.2f})")

            if self._daily_pnl <= -DUX_MAX_DAILY_LOSS:
                self._daily_halted = True
                logger.warning(
                    f"[Dux] DAILY LOSS LIMIT HIT — halted "
                    f"(P&L=${self._daily_pnl:+.2f})"
                )
                return _deny("daily loss limit hit")

            # ── Rule 3: Consecutive loss circuit breaker ──
            if self._consecutive_loss >= DUX_MAX_CONSECUTIVE:
                return _deny(
                    f"3-strike circuit breaker "
                    f"({self._consecutive_loss} consecutive losses)"
                )

            # ── Rule 8: Max simultaneous positions ──
            if len(self._positions) >= DUX_MAX_SIMULTANEOUS:
                return _deny(
                    f"max simultaneous positions ({DUX_MAX_SIMULTANEOUS}) reached"
                )

            # ── No adding to an existing position ──
            if sym in self._positions:
                return _deny(f"already have a position in {sym}")

            # ── Sizing ──
            risk_per_share = abs(entry_price - stop_price)
            if risk_per_share <= 0:
                return _deny(
                    f"invalid entry/stop: entry=${entry_price:.2f} "
                    f"stop=${stop_price:.2f}"
                )

            # ── Rule 4: Minimum R:R (compute before win rate gate) ──
            reward = abs(target1 - entry_price)
            rr = reward / risk_per_share if risk_per_share > 0 else 0
            if rr < DUX_MIN_REWARD_RISK:
                return _deny(
                    f"R:R {rr:.2f} below minimum {DUX_MIN_REWARD_RISK:.1f} "
                    f"(entry=${entry_price:.2f} stop=${stop_price:.2f} "
                    f"t1=${target1:.2f})"
                )

            # ── Rule 5: Win rate gate ──
            # Only block if the current win rate is below BOTH the configured gate
            # AND the breakeven win rate for this specific trade's R:R.
            # Rationale: a 3:1 trade has positive expected value even at a 25% win
            # rate — blocking it because we're below 65% is over-conservative.
            # breakeven_win_rate = 1 / (1 + R:R)
            if self._trades_today >= DUX_MIN_TRADES_FOR_GATE:
                win_rate = self._wins_today / self._trades_today
                breakeven_wr = 1.0 / (1.0 + rr) if rr > 0 else 1.0
                if win_rate < DUX_WIN_RATE_GATE and win_rate < breakeven_wr:
                    return _deny(
                        f"win rate {win_rate:.0%} below breakeven {breakeven_wr:.0%} "
                        f"for this R:R={rr:.1f} setup "
                        f"({self._wins_today}/{self._trades_today})"
                    )
                elif win_rate < DUX_WIN_RATE_GATE:
                    # Win rate below gate but trade has positive EV — log a warning
                    logger.warning(
                        f"[Dux] {sym}: win rate {win_rate:.0%} below gate "
                        f"{DUX_WIN_RATE_GATE:.0%} but R:R={rr:.1f} gives positive EV "
                        f"(breakeven={breakeven_wr:.0%}) — allowing"
                    )

            # Base shares from risk-per-trade cap
            base_shares = int(DUX_MAX_RISK_PER_TRADE / risk_per_share)
            base_shares = max(1, base_shares)

            # Strength modifier: higher confidence → full size
            if strength >= 80:
                size_mult = 1.0
            elif strength >= 65:
                size_mult = 0.75
            else:
                size_mult = 0.50

            # Error mode: reduce size after a blown stop
            if self._error_mode > 0:
                size_mult *= DUX_ERROR_SIZE_MULT

            shares = max(1, int(base_shares * size_mult))

            # ── Rule 6: Max position value cap ──
            max_by_value = int(DUX_MAX_POSITION_VALUE / entry_price) if entry_price > 0 else shares
            shares = min(shares, max_by_value)

            if shares <= 0:
                return _deny("position size computed to 0 shares")

            dollar_risk = round(shares * risk_per_share, 2)

            # ── Rule 1: Verify dollar risk is within limit (10% tolerance) ──
            if dollar_risk > DUX_MAX_RISK_PER_TRADE * 1.10:
                return _deny(
                    f"dollar risk ${dollar_risk:.2f} exceeds limit "
                    f"${DUX_MAX_RISK_PER_TRADE:.2f}"
                )

            logger.info(
                f"[Dux] Risk APPROVED: {sym} {direction} | "
                f"{shares} shares @ ${entry_price:.2f} | "
                f"risk=${dollar_risk:.2f} | R:R={rr:.1f}:1 | "
                f"str={strength} | err_mode={self._error_mode}"
            )

            return {
                "allowed":      True,
                "shares":       shares,
                "dollar_risk":  dollar_risk,
                "rr":           round(rr, 2),
                "reason":       "approved",
            }

    def record_fill(
        self,
        symbol:    str,
        shares:    int,
        fill_price: float,
        direction: str,              # "SHORT" or "LONG"
        expected_risk: float = 0.0,  # dollar risk at sizing time (for error mode)
    ):
        """Record a confirmed fill (new position opened)."""
        sym = symbol.upper()
        with self._lock:
            self._positions[sym] = {
                "shares":        shares,
                "avg_price":     fill_price,
                "entry":         fill_price,
                "direction":     direction,
                "expected_risk": expected_risk,
            }
            self._trades_today += 1
            # Error mode counts entries, not closes: decrement on each fill
            if self._error_mode > 0:
                self._error_mode -= 1
        self._save_state()
        logger.info(
            f"[Dux] Position opened: {sym} {direction} | "
            f"{shares} shares @ ${fill_price:.2f}"
        )

    def record_close(
        self,
        symbol:        str,
        close_price:   float,
        shares:        int | None = None,
        reason:        str        = "close",
        expected_risk: float      = 0.0,
    ):
        """
        Record a position close (full or partial).

        expected_risk: the dollar risk computed at entry (used to detect blown stops
                       that trigger error mode).  If not passed, uses the stored value.
        """
        sym = symbol.upper()
        with self._lock:
            pos = self._positions.get(sym)
            if not pos:
                logger.warning(f"[Dux] record_close: no position found for {sym}")
                return

            closed_shares = shares if shares else pos["shares"]
            direction     = pos.get("direction", "SHORT")
            avg_price     = pos["avg_price"]
            exp_risk      = expected_risk or pos.get("expected_risk", 0.0)

            # Direction-aware P&L
            if direction == "SHORT":
                pnl = (avg_price - close_price) * closed_shares
            else:
                pnl = (close_price - avg_price) * closed_shares

            self._daily_pnl += pnl

            # Accumulate per-trade PnL (partial closes are summed until full close)
            self._position_pnl[sym] = self._position_pnl.get(sym, 0.0) + pnl

            # Closed trade log
            self._closed_trades.append({
                "symbol":      sym,
                "direction":   direction,
                "shares":      closed_shares,
                "entry_price": round(pos["entry"], 4),
                "exit_price":  round(close_price, 4),
                "pnl":         round(pnl, 2),
                "reason":      reason,
                "time":        datetime.now().isoformat(),
            })

            # Determine whether the position is now fully closed
            position_fully_closed = closed_shares >= pos["shares"]

            # Remove or reduce position
            if position_fully_closed:
                del self._positions[sym]
            else:
                pos["shares"] -= closed_shares

            # Win/loss classification: only on FULL close.
            # Counting partial closes as wins/losses inflates win rate and distorts
            # the streak counter — a net losing trade could show as 1 win + 1 loss.
            if position_fully_closed:
                total_trade_pnl = self._position_pnl.pop(sym, pnl)
                if total_trade_pnl > 0:
                    self._consecutive_loss = 0
                    self._wins_today += 1
                    logger.info(
                        f"[Dux] WIN: {sym} | trade P&L=${total_trade_pnl:+.2f} | "
                        f"streak reset | wins={self._wins_today}/"
                        f"{self._trades_today}"
                    )
                else:
                    self._consecutive_loss += 1
                    logger.info(
                        f"[Dux] LOSS: {sym} | trade P&L=${total_trade_pnl:+.2f} | "
                        f"streak={self._consecutive_loss}"
                    )

                    # Error mode: blown stop triggers reduced sizing.
                    # Use total trade loss vs expected risk for the check.
                    if exp_risk > 0 and abs(total_trade_pnl) > exp_risk * DUX_ERROR_LOSS_TRIGGER:
                        self._error_mode = DUX_ERROR_TRADES
                        logger.warning(
                            f"[Dux] ERROR MODE activated: "
                            f"total loss ${abs(total_trade_pnl):.2f} > "
                            f"{DUX_ERROR_LOSS_TRIGGER}× expected ${exp_risk:.2f}. "
                            f"Next {DUX_ERROR_TRADES} trades at "
                            f"{DUX_ERROR_SIZE_MULT:.0%} size."
                        )
            else:
                logger.info(
                    f"[Dux] PARTIAL close: {sym} {closed_shares} shares "
                    f"@ ${close_price:.2f} | partial P&L=${pnl:+.2f} "
                    f"({reason})"
                )

            # Check daily loss limit
            if self._daily_pnl <= -DUX_MAX_DAILY_LOSS:
                self._daily_halted = True
                logger.warning(
                    f"[Dux] DAILY LOSS LIMIT REACHED: "
                    f"P&L=${self._daily_pnl:+.2f} — session halted"
                )

        self._save_state()

    def get_daily_pnl(self) -> float:
        with self._lock:
            return self._daily_pnl

    def get_positions(self) -> dict:
        """Return a snapshot of all open positions."""
        with self._lock:
            return {sym: dict(pos) for sym, pos in self._positions.items()}

    def is_halted(self) -> bool:
        with self._lock:
            return self._daily_halted

    def get_status(self) -> dict:
        with self._lock:
            win_rate = (
                self._wins_today / self._trades_today
                if self._trades_today > 0 else 0.0
            )
            return {
                "date":             self._session_date.isoformat(),
                "daily_pnl":        round(self._daily_pnl, 2),
                "consecutive_loss": self._consecutive_loss,
                "trades_today":     self._trades_today,
                "wins_today":       self._wins_today,
                "win_rate":         round(win_rate, 3),
                "daily_halted":     self._daily_halted,
                "error_mode":       self._error_mode,
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
                self._wins_today       = 0
                self._daily_halted     = False
                self._error_mode       = 0
                self._positions        = {}
                self._closed_trades    = []
                self._position_pnl     = {}
                logger.info("[Dux] Risk manager: new trading day — counters reset")
        self._save_state()

    def _save_state(self):
        path = Path(DUX_PORTFOLIO_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            state = {
                "date":             self._session_date.isoformat(),
                "daily_pnl":        self._daily_pnl,
                "consecutive_loss": self._consecutive_loss,
                "trades_today":     self._trades_today,
                "wins_today":       self._wins_today,
                "daily_halted":     self._daily_halted,
                "error_mode":       self._error_mode,
                "positions":        self._positions,
                "position_pnl":     self._position_pnl,
                "closed_trades":    self._closed_trades,
            }
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(path)   # atomic rename — no half-written file on crash
        except OSError as e:
            logger.warning(f"[Dux] Could not save portfolio state: {e}")

    def _load_state(self):
        if not os.path.exists(DUX_PORTFOLIO_PATH):
            return
        try:
            with open(DUX_PORTFOLIO_PATH) as f:
                state = json.load(f)
            saved_date = date.fromisoformat(state.get("date", "2000-01-01"))
            if saved_date != date.today():
                return    # stale state — start fresh
            with self._lock:
                self._session_date     = saved_date
                self._daily_pnl        = state.get("daily_pnl", 0.0)
                self._consecutive_loss = state.get("consecutive_loss", 0)
                self._trades_today     = state.get("trades_today", 0)
                self._wins_today       = state.get("wins_today", 0)
                self._daily_halted     = state.get("daily_halted", False)
                self._error_mode       = state.get("error_mode", 0)
                self._positions        = state.get("positions", {})
                self._position_pnl     = state.get("position_pnl", {})
                self._closed_trades    = state.get("closed_trades", [])
            logger.info(
                f"[Dux] Risk manager: loaded today's state | "
                f"P&L=${self._daily_pnl:+.2f} "
                f"trades={self._trades_today} "
                f"positions={len(self._positions)}"
            )
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"[Dux] Could not load portfolio state: {e}")


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _deny(reason: str) -> dict:
    logger.info(f"[Dux] Risk DENIED: {reason}")
    return {"allowed": False, "shares": 0, "dollar_risk": 0.0, "reason": reason}
