"""
PDT Rule Compliance Tracker.
Max 3 day trades per rolling 5 business days.
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from loguru import logger


class DayTradeTracker:

    LOG_PATH = "config/day_trade_log.json"
    MAX_DAY_TRADES = 3
    WINDOW_DAYS = 5

    def __init__(self):
        self._log = self._load_log()

    def _load_log(self):
        if Path(self.LOG_PATH).exists():
            with open(self.LOG_PATH) as f:
                return json.load(f)
        return []

    def _save_log(self):
        Path(self.LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(self.LOG_PATH, "w") as f:
            json.dump(self._log, f, indent=2, default=str)

    def _biz_days_ago(self, n):
        current = date.today()
        count = 0
        while count < n:
            current -= timedelta(days=1)
            if current.weekday() < 5:
                count += 1
        return current

    def get_recent(self):
        cutoff = self._biz_days_ago(self.WINDOW_DAYS)
        return [t for t in self._log if datetime.strptime(t["date"], "%Y-%m-%d").date() >= cutoff]

    def remaining(self):
        return max(0, self.MAX_DAY_TRADES - len(self.get_recent()))

    def can_day_trade(self):
        return self.remaining() > 0

    def record(self, symbol, reason="emergency_stop"):
        entry = {"date": date.today().isoformat(), "symbol": symbol, "reason": reason, "timestamp": datetime.utcnow().isoformat()}
        self._log.append(entry)
        self._save_log()
        r = self.remaining()
        logger.warning(f"DAY TRADE USED: {symbol} ({reason}). Remaining: {r}/{self.MAX_DAY_TRADES}")
        if r <= 1:
            logger.warning("CRITICAL: Only 1 day trade remaining!")
        if r == 0:
            logger.warning("NO DAY TRADES LEFT")

    def should_allow_emergency(self, symbol, loss_pct):
        if not self.can_day_trade():
            logger.warning(f"Cannot same-day exit {symbol}: no day trades left")
            return False
        if loss_pct >= 0.05:
            logger.warning(f"Emergency exit authorized for {symbol} (loss: {loss_pct:.1%})")
            return True
        logger.info(f"Day trade not authorized for {symbol} (loss {loss_pct:.1%} < 5%)")
        return False

    def get_status(self):
        recent = self.get_recent()
        return {"remaining": self.remaining(), "used_in_window": len(recent), "max_allowed": self.MAX_DAY_TRADES, "recent_trades": recent, "can_day_trade": self.can_day_trade()}
