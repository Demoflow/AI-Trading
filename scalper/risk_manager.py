"""
Scalper Risk Manager v5 - Long Options Only.
- Long calls and puts only (no premium selling)
- Uncapped daily trades (settled cash is the natural limit)
- 2% of equity per trade, confidence-scaled
- Dynamic equity sync: position size grows as account grows
- Time-of-day gamma-aware scaling for directional
"""

import os
import json
from pathlib import Path
from datetime import datetime, date
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None

_RISK_STATE_PATH = "config/scalper_risk_state.json"


def _hour_ct() -> float:
    n = datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()
    return n.hour + n.minute / 60.0 + n.second / 3600.0

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
    MAX_DAILY_LOSS_PCT = 0.08
    MAX_HOLD_MINUTES = 30    # Default; _get_directional_targets() returns time-aware value
    MAX_OPEN_POSITIONS = 5   # Default; overridden by day type + GEX via get_max_positions()

    # Day-type / GEX aware position caps
    # QUIET/CHOPPY + POSITIVE GEX: market is pinned — one position at a time.
    _POS_CAP = {
        ("QUIET",    "POSITIVE"): 1,
        ("CHOPPY",   "POSITIVE"): 1,
        ("QUIET",    "NEGATIVE"): 2,
        ("QUIET",    "NEUTRAL"):  2,
        ("CHOPPY",   "NEGATIVE"): 2,
        ("CHOPPY",   "NEUTRAL"):  2,
    }
    # Default for TRENDING / VOLATILE (or any combo not listed above) = 5

    # Minimum confidence overrides by day type
    _MIN_CONF = {
        "QUIET":   85,
        "CHOPPY":  75,
    }
    MIN_CONF_DEFAULT = 70

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
        self.day_type = ""       # Set by main loop after classification
        self.gex_regime = ""     # Set by main loop after GEX update
        self._consecutive_losses = 0  # Reset to 0 on any winning trade
        self._load_state()

    def _load_state(self):
        """Restore in-memory risk state from disk (survives process restarts)."""
        try:
            if os.path.exists(_RISK_STATE_PATH):
                with open(_RISK_STATE_PATH) as f:
                    s = json.load(f)
                if s.get("date") == date.today().isoformat():
                    self.trades_today        = s.get("trades_today", 0)
                    self.daily_pnl           = s.get("daily_pnl", 0.0)
                    self._consecutive_losses = s.get("consecutive_losses", 0)
                    self.shutdown            = s.get("shutdown", False)
                    logger.info(
                        f"Risk state restored: trades={self.trades_today} "
                        f"pnl=${self.daily_pnl:+,.2f} "
                        f"streak={self._consecutive_losses} "
                        f"shutdown={self.shutdown}"
                    )
        except Exception as e:
            logger.warning(f"Risk state load failed (starting fresh): {e}")

    def _save_state(self):
        """Persist in-memory risk state to disk atomically."""
        try:
            state = {
                "date":               self._trade_date.isoformat(),
                "trades_today":       self.trades_today,
                "daily_pnl":          self.daily_pnl,
                "consecutive_losses": self._consecutive_losses,
                "shutdown":           self.shutdown,
            }
            path = Path(_RISK_STATE_PATH)
            path.parent.mkdir(exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.replace(path)
        except Exception as e:
            logger.warning(f"Risk state save failed: {e}")

    def _reset_if_new_day(self):
        if date.today() != self._trade_date:
            self.trades_today = 0
            self.daily_pnl = 0
            self.shutdown = False
            self._consecutive_losses = 0
            self._trade_date = date.today()
            self._save_state()

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
        now = datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()
        if now.weekday() >= 5:
            return False, "weekend"
        h = _hour_ct()
        is_event, etype = self.is_event_day()
        if is_event and h < 9.5:
            return False, f"wait_{etype}"
        if h < self.MORNING_START:
            return False, "pre_market"
        if h >= self.POWER_HOUR_END:
            return False, "closed"
        # Time-of-day window gating is handled by TimeContextFilter (time_context.py).
        # risk_manager only enforces the outer session boundary; intra-session
        # chop-zone confidence boosts and entry_allowed flags live in time_context.
        return True, "active"

    def can_trade(self):
        self._reset_if_new_day()
        if self.shutdown:
            return False, "shutdown"
        if self.daily_pnl <= -(self.equity * self.MAX_DAILY_LOSS_PCT):
            self.shutdown = True
            self._save_state()
            logger.warning(f"DAILY LOSS: ${self.daily_pnl:+,.2f}")
            return False, "max_loss"
        # Consecutive loss circuit breaker: 5 straight losses → sit out the rest of the session
        if self._consecutive_losses >= 5:
            self.shutdown = True
            self._save_state()
            logger.warning(
                f"CONSECUTIVE LOSS LIMIT: {self._consecutive_losses} straight losses — "
                f"sitting out rest of session to prevent runaway drawdown"
            )
            return False, "consec_loss_limit"
        cap = self.get_max_positions(self.day_type, self.gex_regime)
        if self.open_positions >= cap:
            return False, f"max_open({cap})"
        ok, reason = self.is_trading_window()
        if not ok:
            return False, reason
        return True, "ok"

    def update_equity(self, equity):
        """Sync equity so position sizing scales with account growth."""
        if equity > 0:
            self.equity = equity

    def get_max_positions(self, day_type="", gex_regime=""):
        """Return position cap based on day classification and GEX regime."""
        return self._POS_CAP.get((day_type, gex_regime), self.MAX_OPEN_POSITIONS)

    def get_min_confidence(self, day_type=""):
        """Return minimum signal confidence based on day classification and streak."""
        base = self._MIN_CONF.get(day_type, self.MIN_CONF_DEFAULT)
        # Raise the bar after 3 consecutive losses — only exceptional setups qualify
        if self._consecutive_losses >= 3:
            base += 15
            logger.debug(
                f"Consecutive loss boost active ({self._consecutive_losses} in a row): "
                f"min_conf now {base}"
            )
        return base

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
        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= 3:
                logger.warning(
                    f"Streak: {self._consecutive_losses} consecutive losses "
                    f"(${pnl:+,.2f}) — raising min confidence"
                )
        else:
            if self._consecutive_losses > 0:
                logger.info(f"Streak broken after {self._consecutive_losses} losses")
            self._consecutive_losses = 0
        self._save_state()

    def check_exit(self, position, current_value):
        """
        Exit logic for long calls and puts.
        Returns (should_exit, reason, action)
        """
        return self._exit_directional(position, current_value)

    def _get_directional_targets(self):
        """
        Time-of-day aware targets for long options.
        Returns: (profit_target, stop_loss, trail_start, trail_pct, breakeven_trigger, max_hold_min)

        All windows target ~2.5:1 R:R (profit / stop).
        Stops tighten as theta decay accelerates through the day.
        """
        h = _hour_ct()
        #                    profit  stop  trail_start  trail_pct  be_trigger  max_hold
        if h < 10.0:       # Opening — wider gamma range, 30 min hold OK
            return          0.50,   0.20,   0.12,        0.10,      0.20,       30
        elif h < 13.0:     # First pullback + chop zone
            return          0.40,   0.15,   0.10,        0.08,      0.18,       25
        elif h < 14.0:     # Post-lunch directional
            return          0.30,   0.12,   0.08,        0.07,      0.15,       20
        else:              # Gamma zone — theta accelerating, exit fast
            return          0.25,   0.10,   0.07,        0.06,      0.12,       15

    def _exit_directional(self, pos, current_value):
        """
        Exit logic for long options (calls/puts).

        Priority order:
          1. Full profit target
          2. Hard stop loss
          3. Breakeven stop — once peak exceeded be_trigger, never fall below +2%
          4. Trailing stop — activates at trail_start gain, fires trail_pct below peak
          5. Time stop — time-of-day aware max hold
          6. Closing gate — exits before EOD force-close window
        """
        entry_cost = pos.get("entry_cost", 0)
        entry_time = pos.get("entry_time")
        if entry_cost <= 0:
            return False, "no_data", None

        pnl_pct = (current_value - entry_cost) / entry_cost
        profit_t, stop_t, trail_start, trail_pct, be_trigger, max_hold = (
            self._get_directional_targets()
        )

        # 1. Full profit target
        if pnl_pct >= profit_t:
            return True, f"profit_{pnl_pct:+.0%}", None

        # 2. Hard stop loss
        if pnl_pct <= -stop_t:
            return True, f"stop_{pnl_pct:+.0%}", None

        # 3. Breakeven stop: once the position peaked above be_trigger,
        #    never let it fall back below +2% (protect meaningful gains)
        peak = pos.get("peak_value", current_value)
        if entry_cost > 0 and peak > 0:
            peak_pct = (peak - entry_cost) / entry_cost
            if peak_pct >= be_trigger and pnl_pct <= 0.02:
                return True, f"breakeven_{pnl_pct:+.0%}", None

            # 4. Trailing stop: activates at trail_start gain,
            #    fires if position retreats trail_pct below its peak
            if pnl_pct >= trail_start:
                from_peak = (current_value - peak) / peak
                if from_peak <= -trail_pct:
                    return True, f"trail_{from_peak:+.0%}", None

        # 5. Time stop (tightens later in the day as theta accelerates)
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            now_dt = datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()
            if entry_time.tzinfo is None and now_dt.tzinfo is not None:
                entry_time = entry_time.replace(tzinfo=_CT_TZ)
            mins = (now_dt - entry_time).total_seconds() / 60
            if mins >= max_hold:
                return True, f"time_{mins:.0f}min", None

        # 6. Closing gate — align with EOD force-close at 2:45 PM CT
        h = _hour_ct()
        if h >= 14.75:
            return True, "closing", None

        return False, "hold", None
