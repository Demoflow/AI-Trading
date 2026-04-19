"""
Scalper Risk Manager v6 — Stock VWAP Scalping.
Complete rewrite for stocks (no options/premium/Greeks).

- Position sizing in SHARES and DOLLAR NOTIONAL
- Risk per trade = shares x stop_distance_per_share
- Aggressive growth: 1.5-2% risk per trade, confidence-scaled
- Daily loss limit: 5% of equity (hard stop)
- Max 3 simultaneous positions
- Consecutive loss circuit breaker (5 straight losses)
- State persistence to disk
- CT timezone throughout
"""

import os
import json
from pathlib import Path
from datetime import datetime, date
from loguru import logger

from utils.time_helpers import now_ct, hour_ct, today_ct, CT_TZ

# Anchor path relative to this file — works regardless of working directory
_RISK_STATE_PATH = Path(__file__).parent.parent / "config" / "scalper_risk_state.json"

# Legacy aliases kept for any internal callers
_now_ct = now_ct
_hour_ct = hour_ct


def _load_event_dates():
    """
    Load FOMC/CPI dates from config/event_dates.json.
    Falls back to hardcoded 2026 dates if the file is missing.
    Logs a WARNING at startup if no dates exist for the current year.
    """
    config_path = Path(__file__).parent.parent / "config" / "event_dates.json"
    current_year = today_ct().year
    try:
        if config_path.exists():
            with open(config_path) as f:
                data = json.load(f)
            fomc = data.get("fomc", [])
            cpi  = data.get("cpi",  [])
        else:
            raise FileNotFoundError
    except Exception:
        # Hardcoded fallback — update event_dates.json annually
        fomc = [
            "2026-01-29", "2026-03-18", "2026-05-06",
            "2026-06-17", "2026-07-29", "2026-09-16",
            "2026-10-28", "2026-12-09",
        ]
        cpi = [
            "2026-01-14", "2026-02-12", "2026-03-11",
            "2026-04-14", "2026-05-13", "2026-06-10",
            "2026-07-15", "2026-08-12", "2026-09-16",
            "2026-10-14", "2026-11-12", "2026-12-09",
        ]

    # Warn if no dates exist for current year
    all_dates = fomc + cpi
    years_present = {d[:4] for d in all_dates}
    if str(current_year) not in years_present:
        logger.warning(
            f"EVENT DATES: no FOMC/CPI dates found for {current_year}. "
            f"Update config/event_dates.json — event-day risk reduction is DISABLED."
        )

    return set(fomc), set(cpi)


FOMC_DATES, CPI_DATES = _load_event_dates()


class ScalperRiskManager:
    """Stock-based risk manager for VWAP scalping."""

    MAX_DAILY_LOSS_PCT = 0.05       # 5% of equity hard stop
    MAX_OPEN_POSITIONS = 3          # Max simultaneous stock positions
    MAX_RISK_PER_TRADE = 0.02       # Absolute cap: 2% of equity per trade
    CONSECUTIVE_LOSS_LIMIT = 5      # Circuit breaker

    # Time windows (CT)
    MORNING_START = 8.5             # 8:30 AM CT
    NO_TRADE_UNTIL = 9.0            # No trades first 30 min (9:00 AM CT = 10:00 AM ET)
    LUNCH_START = 10.5              # 10:30 AM CT
    LUNCH_END = 13.0                # 1:00 PM CT
    EOD_CUTOFF = 15.5               # 3:30 PM CT — force close
    MARKET_CLOSE = 15.0             # 3:00 PM CT

    def __init__(self, equity):
        self.equity = equity
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.open_positions = 0
        self.shutdown = False
        self._trade_date = today_ct()
        self.day_type = ""
        self.gex_regime = ""
        self._consecutive_losses = 0
        self._load_state()

    # ── STATE PERSISTENCE ────────────────────────────────────────────────────

    def _load_state(self):
        """Restore risk state from disk (survives process restarts)."""
        try:
            if os.path.exists(_RISK_STATE_PATH):
                with open(_RISK_STATE_PATH) as f:
                    s = json.load(f)
                if s.get("date") == today_ct().isoformat():
                    self.trades_today = s.get("trades_today", 0)
                    self.daily_pnl = s.get("daily_pnl", 0.0)
                    self._consecutive_losses = s.get("consecutive_losses", 0)
                    self.shutdown = s.get("shutdown", False)
                    logger.info(
                        f"Risk state restored: trades={self.trades_today} "
                        f"pnl=${self.daily_pnl:+,.2f} "
                        f"streak={self._consecutive_losses} "
                        f"shutdown={self.shutdown}"
                    )
        except Exception as e:
            logger.warning(f"Risk state load failed (starting fresh): {e}")

    def _save_state(self):
        """Persist risk state to disk atomically."""
        try:
            state = {
                "date": self._trade_date.isoformat(),
                "trades_today": self.trades_today,
                "daily_pnl": self.daily_pnl,
                "consecutive_losses": self._consecutive_losses,
                "shutdown": self.shutdown,
            }
            path = Path(_RISK_STATE_PATH)
            path.parent.mkdir(exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.replace(path)
        except Exception as e:
            logger.warning(f"Risk state save failed: {e}")

    def _reset_if_new_day(self):
        if today_ct() != self._trade_date:
            self.trades_today = 0
            self.daily_pnl = 0.0
            self.shutdown = False
            self._consecutive_losses = 0
            self._trade_date = today_ct()
            self._save_state()

    # ── TRADING WINDOW ───────────────────────────────────────────────────────

    def is_event_day(self):
        today = today_ct().isoformat()
        if today in FOMC_DATES:
            return True, "FOMC"
        if today in CPI_DATES:
            return True, "CPI"
        d = today_ct()
        if d.weekday() == 4 and d.day <= 7:
            return True, "NFP"
        return False, ""

    def is_trading_window(self):
        """Check if we're in a valid trading window."""
        self._reset_if_new_day()
        now = _now_ct()
        if now.weekday() >= 5:
            return False, "weekend"
        h = _hour_ct()
        is_event, etype = self.is_event_day()
        if is_event and h < 9.5:
            return False, f"wait_{etype}"
        if h < self.MORNING_START:
            return False, "pre_market"
        if h >= self.MARKET_CLOSE:
            return False, "closed"
        return True, "active"

    # ── CAN TRADE ────────────────────────────────────────────────────────────

    def can_trade(self):
        """Check if a new trade is allowed."""
        self._reset_if_new_day()
        if self.shutdown:
            return False, "shutdown"
        # Daily loss limit (5% hard stop)
        if self.daily_pnl <= -(self.equity * self.MAX_DAILY_LOSS_PCT):
            self.shutdown = True
            self._save_state()
            logger.warning(f"DAILY LOSS LIMIT: ${self.daily_pnl:+,.2f} (5% of ${self.equity:,.0f})")
            return False, "max_loss"
        # Consecutive loss circuit breaker
        if self._consecutive_losses >= self.CONSECUTIVE_LOSS_LIMIT:
            self.shutdown = True
            self._save_state()
            logger.warning(
                f"CONSECUTIVE LOSS LIMIT: {self._consecutive_losses} straight losses"
            )
            return False, "consec_loss_limit"
        # Max open positions
        if self.open_positions >= self.MAX_OPEN_POSITIONS:
            return False, f"max_open({self.MAX_OPEN_POSITIONS})"
        # Trading window
        ok, reason = self.is_trading_window()
        if not ok:
            return False, reason
        return True, "ok"

    # ── EQUITY SYNC ──────────────────────────────────────────────────────────

    def update_equity(self, equity):
        """Sync equity so position sizing scales with account growth."""
        if equity > 0:
            self.equity = equity

    # ── POSITION SIZING ──────────────────────────────────────────────────────

    def get_position_size(self, symbol, price, stop_price, confidence,
                          position_limit=None):
        """
        Calculate position size in shares and dollar notional.

        Args:
            symbol: ticker
            price: entry price per share
            stop_price: stop loss price per share
            confidence: signal confidence (0-100)
            position_limit: max dollar notional from StockUniverse (optional)

        Returns:
            (share_count, dollar_notional) tuple, or (0, 0) if trade rejected.
        """
        if price <= 0 or stop_price <= 0:
            return 0, 0.0

        # Determine risk per trade based on confidence
        if confidence >= 85:
            risk_pct = 0.020  # 2.0% of equity (full aggressive)
        elif confidence >= 75:
            risk_pct = 0.015  # 1.5% of equity
        elif confidence >= 65:
            risk_pct = 0.010  # 1.0% of equity
        else:
            return 0, 0.0     # Below minimum — skip

        # Hard cap at MAX_RISK_PER_TRADE (2%)
        risk_pct = min(risk_pct, self.MAX_RISK_PER_TRADE)

        # Dollar risk budget for this trade
        dollar_risk = self.equity * risk_pct

        # Stop distance per share
        stop_distance = abs(price - stop_price)
        if stop_distance <= 0:
            return 0, 0.0

        # Shares = risk budget / risk per share
        share_count = int(dollar_risk / stop_distance)
        if share_count < 1:
            return 0, 0.0

        dollar_notional = share_count * price

        # Apply position limit from StockUniverse
        if position_limit and dollar_notional > position_limit:
            share_count = int(position_limit / price)
            dollar_notional = share_count * price

        # Verify the actual risk doesn't exceed 2% of equity
        actual_risk = share_count * stop_distance
        if actual_risk > self.equity * self.MAX_RISK_PER_TRADE:
            share_count = int(self.equity * self.MAX_RISK_PER_TRADE / stop_distance)
            dollar_notional = share_count * price

        if share_count < 1:
            return 0, 0.0

        return share_count, round(dollar_notional, 2)

    def get_dollar_risk(self, share_count, price, stop_price):
        """Calculate the dollar amount at risk for a position."""
        stop_distance = abs(price - stop_price)
        return round(share_count * stop_distance, 2)

    def get_share_count(self, symbol, price, stop_price, confidence,
                        position_limit=None):
        """Convenience wrapper: returns just share count."""
        shares, _ = self.get_position_size(symbol, price, stop_price,
                                           confidence, position_limit)
        return shares

    # ── MIN CONFIDENCE THRESHOLDS ────────────────────────────────────────────

    def get_min_confidence(self, day_type="", symbol_tier=1):
        """
        Return minimum signal confidence based on day type and loss streak.
        Symbol tier minimums are enforced by StockUniverse.get_min_confidence().
        """
        if day_type == "QUIET":
            base = 85
        elif day_type == "CHOPPY":
            base = 78
        elif day_type == "RANGE_BOUND":
            base = 75
        else:
            base = 70

        # Raise bar after 3 consecutive losses
        if self._consecutive_losses >= 3:
            base += 15
            logger.debug(
                f"Streak boost active ({self._consecutive_losses} losses): "
                f"min_conf now {base}"
            )

        return base

    # ── TRADE RECORDING ──────────────────────────────────────────────────────

    def record_trade(self, pnl=0):
        """Record a completed trade result."""
        self.trades_today += 1
        self.daily_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= 3:
                logger.warning(
                    f"Streak: {self._consecutive_losses} consecutive losses "
                    f"(${pnl:+,.2f})"
                )
        else:
            if self._consecutive_losses > 0:
                logger.info(f"Streak broken after {self._consecutive_losses} losses")
            self._consecutive_losses = 0
        self._save_state()

    # ── MAX POSITIONS ────────────────────────────────────────────────────────

    def get_max_positions(self, day_type="", gex_regime=""):
        """Return position cap based on day type."""
        if day_type in ("QUIET", "CHOPPY"):
            return 2
        return self.MAX_OPEN_POSITIONS
