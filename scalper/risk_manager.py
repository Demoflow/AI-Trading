"""
Scalper Risk Manager v4 - Strategy-Specific Exits.
- Naked/straddle/strangle have separate exit logic
- Partial profit taking (scale out)
- Proper premium selling exits (50% profit, 2x stop)
- Close all selling positions by 3:30 PM ET
- Time-of-day gamma-aware scaling for directional
"""

import os
from datetime import datetime, date
from loguru import logger

FOMC_DATES = [
    "2026-01-29", "2026-03-18", "2026-05-06",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09",
]
CPI_DATES = [
    "2026-01-14", "2026-02-12", "2026-03-11",
    "2026-04-14", "2026-05-13", "2026-06-10",
    "2026-07-15", "2026-08-12", "2026-09-16",
    "2026-10-14", "2026-11-12", "2026-12-09",
]


class ScalperRiskManager:

    MAX_RISK_PER_TRADE = 0.02
    MAX_TRADES_PER_DAY = 8
    MAX_DAILY_LOSS_PCT = 0.08
    MAX_HOLD_MINUTES = 30
    MAX_OPEN_POSITIONS = 3

    LUNCH_START = 10.5
    LUNCH_END = 13.0
    MORNING_START = 8.5
    MORNING_END = 10.5
    AFTERNOON_START = 13.0
    AFTERNOON_END = 14.5
    POWER_HOUR_START = 14.5
    POWER_HOUR_END = 15.0

    def __init__(self, equity):
        self.equity = equity
        self.trades_today = 0
        self.daily_pnl = 0
        self.open_positions = 0
        self.shutdown = False
        self._trade_date = date.today()

    def _reset_if_new_day(self):
        if date.today() != self._trade_date:
            self.trades_today = 0
            self.daily_pnl = 0
            self.shutdown = False
            self._trade_date = date.today()

    def is_event_day(self):
        today = date.today().isoformat()
        if today in FOMC_DATES:
            return True, "FOMC"
        if today in CPI_DATES:
            return True, "CPI"
        d = date.today()
        if d.weekday() == 4 and d.day <= 7:
            return True, "NFP"
        return False, ""

    def is_trading_window(self):
        self._reset_if_new_day()
        now = datetime.now()
        if now.weekday() >= 5:
            return False, "weekend"
        h = now.hour + now.minute / 60.0
        is_event, etype = self.is_event_day()
        if is_event and h < 9.5:
            return False, f"wait_{etype}"
        if h < self.MORNING_START:
            return False, "pre_market"
        if h >= self.POWER_HOUR_END:
            return False, "closed"
        if self.LUNCH_START <= h < self.LUNCH_END:
            return False, "lunch"
        if self.MORNING_START <= h < self.MORNING_END:
            return True, "morning"
        if self.AFTERNOON_START <= h < self.AFTERNOON_END:
            return True, "afternoon"
        if self.POWER_HOUR_START <= h < self.POWER_HOUR_END:
            return True, "power_hour"
        return False, "between"

    def can_trade(self):
        self._reset_if_new_day()
        if self.shutdown:
            return False, "shutdown"
        if self.daily_pnl <= -(self.equity * self.MAX_DAILY_LOSS_PCT):
            self.shutdown = True
            logger.warning(f"DAILY LOSS: ${self.daily_pnl:+,.2f}")
            return False, "max_loss"
        if self.trades_today >= self.MAX_TRADES_PER_DAY:
            return False, "max_trades"
        if self.open_positions >= self.MAX_OPEN_POSITIONS:
            return False, "max_open"
        ok, reason = self.is_trading_window()
        if not ok:
            return False, reason
        return True, "ok"

    def get_position_size(self, conf):
        base = self.equity * self.MAX_RISK_PER_TRADE
        if conf >= 85:
            return round(base, 2)
        elif conf >= 75:
            return round(base * 0.75, 2)
        elif conf >= 65:
            return round(base * 0.50, 2)
        return round(base * 0.35, 2)

    def record_trade(self, pnl=0):
        self.trades_today += 1
        self.daily_pnl += pnl

    def check_exit(self, position, current_value):
        """
        Route to strategy-specific exit logic.
        Returns (should_exit, reason, action)
        """
        structure = position.get("structure", "LONG_OPTION")

        if structure == "LONG_OPTION":
            return self._exit_directional(position, current_value)
        elif structure == "CREDIT_SPREAD":
            return self._exit_credit(position, current_value)
        elif structure in ("NAKED_PUT", "NAKED_CALL"):
            return self._exit_naked(position, current_value)
        elif structure in ("STRADDLE", "STRANGLE"):
            return self._exit_straddle_strangle(position, current_value)
        elif structure == "IRON_CONDOR":
            return self._exit_credit(position, current_value)
        elif structure == "RATIO_SPREAD":
            return self._exit_ratio(position, current_value)
        else:
            return self._exit_directional(position, current_value)

    def _get_directional_targets(self):
        """Gamma-aware targets for directional buys."""
        h = datetime.now().hour + datetime.now().minute / 60.0
        if h < 10.0:
            return 0.35, 0.30, -0.12
        elif h < 13.0:
            return 0.30, 0.25, -0.10
        elif h < 14.5:
            return 0.25, 0.20, -0.08
        else:
            return 0.20, 0.15, -0.08

    def _exit_directional(self, pos, current_value):
        """Exit logic for long options (calls/puts)."""
        entry_cost = pos.get("entry_cost", 0)
        entry_time = pos.get("entry_time")
        if entry_cost <= 0:
            return False, "no_data", None

        pnl_pct = (current_value - entry_cost) / entry_cost
        profit_t, stop_t, trail_t = self._get_directional_targets()

        # Full profit target
        if pnl_pct >= profit_t:
            return True, f"profit_{pnl_pct:+.0%}", None

        # Stop loss
        if pnl_pct <= -stop_t:
            # Check if rolling is better
            if self._should_roll(pos, pnl_pct):
                return True, f"roll_{pnl_pct:+.0%}", "ROLL"
            return True, f"stop_{pnl_pct:+.0%}", None

        # Time stop
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            mins = (datetime.now() - entry_time).total_seconds() / 60
            if mins >= self.MAX_HOLD_MINUTES:
                return True, f"time_{mins:.0f}min", None

        # Market closing
        h = datetime.now().hour + datetime.now().minute / 60.0
        if h >= 14.92:
            return True, "closing", None

        # Trailing stop after partial profit
        if pnl_pct >= 0.15:
            peak = pos.get("peak_value", current_value)
            if peak > 0:
                from_peak = (current_value - peak) / peak
                if from_peak <= trail_t:
                    return True, f"trail_{from_peak:+.0%}", None

        return False, "hold", None

    def _exit_credit(self, pos, current_value):
        """
        Exit for credit spreads and iron condors.
        Profit: spread worth < 50% of credit (keep 50%+)
        Stop: spread worth > 2x credit
        Time: close by 3:30 PM ET (2:30 CT)
        """
        credit = pos.get("credit_received", 0)
        if credit <= 0:
            return self._exit_directional(pos, current_value)

        if current_value <= credit * 0.50:
            return True, "credit_profit_50%", None
        if current_value >= credit * 2.0:
            return True, "credit_stop_2x", None

        # Close all premium sells by 3:30 PM ET
        h = datetime.now().hour + datetime.now().minute / 60.0
        if h >= 14.5:
            return True, "credit_eod_close", None

        return False, "hold", None

    def _exit_naked(self, pos, current_value):
        """
        Exit for naked puts/calls.
        Profit: option worth < 50% of premium collected
        Stop: option worth > 2x premium
        Time: close by 3:30 PM ET
        """
        credit = pos.get("credit_received", 0)
        if credit <= 0:
            return self._exit_directional(pos, current_value)

        if current_value <= credit * 0.50:
            return True, "naked_profit_50%", None
        if current_value >= credit * 2.0:
            return True, "naked_stop_2x", None

        h = datetime.now().hour + datetime.now().minute / 60.0
        if h >= 14.5:
            return True, "naked_eod_close", None

        return False, "hold", None

    def _exit_straddle_strangle(self, pos, current_value):
        """
        Exit for short straddles/strangles.
        Profit: total value < 50% of premium
        Stop: total value > 2x premium
        Time: close by 3:30 PM ET
        One-sided risk: if value > 1.5x, alert
        """
        credit = pos.get("credit_received", 0)
        if credit <= 0:
            return self._exit_directional(pos, current_value)

        if current_value <= credit * 0.50:
            return True, "straddle_profit_50%", None
        if current_value >= credit * 2.0:
            return True, "straddle_stop_2x", None

        # Earlier exit at 1.5x as warning
        if current_value >= credit * 1.5:
            h = datetime.now().hour + datetime.now().minute / 60.0
            if h >= 14.0:
                return True, "straddle_risk_1.5x_pm", None

        h = datetime.now().hour + datetime.now().minute / 60.0
        if h >= 14.5:
            return True, "straddle_eod_close", None

        return False, "hold", None

    def _exit_ratio(self, pos, current_value):
        """Exit for ratio spreads."""
        entry_cost = pos.get("entry_cost", 0)
        credit = pos.get("credit_received", 0)

        if credit > 0:
            # Net credit ratio: profit if stays in range
            if current_value <= credit * 0.25:
                return True, "ratio_profit", None
            if current_value >= entry_cost * 0.5:
                return True, "ratio_stop", None
        else:
            pnl_pct = (current_value - entry_cost) / entry_cost if entry_cost > 0 else 0
            if pnl_pct >= 0.50:
                return True, f"ratio_profit_{pnl_pct:+.0%}", None
            if pnl_pct <= -0.40:
                return True, f"ratio_stop_{pnl_pct:+.0%}", None

        h = datetime.now().hour + datetime.now().minute / 60.0
        if h >= 14.75:
            return True, "ratio_eod_close", None

        return False, "hold", None

    def _should_roll(self, pos, pnl_pct):
        if pnl_pct > -0.15 or pnl_pct < -0.30:
            return False
        h = datetime.now().hour + datetime.now().minute / 60.0
        if h >= 14.0:
            return False
        entry_time = pos.get("entry_time")
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            mins = (datetime.now() - entry_time).total_seconds() / 60
            if mins < 15:
                return True
        return False
