"""
Market calendar with holiday awareness.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from loguru import logger


class MarketCalendar:

    def __init__(self):
        self.holidays = set()
        self.early_close = set()
        self._load()

    def _load(self):
        p = Path("config/holidays_2026.json")
        if p.exists():
            with open(p) as f:
                d = json.load(f)
            self.holidays = set(d.get("holidays", []))
            self.early_close = set(d.get("early_close", []))

    def is_market_open_today(self):
        today = date.today()
        if today.weekday() >= 5:
            return False
        if today.isoformat() in self.holidays:
            return False
        return True

    def is_early_close(self):
        return date.today().isoformat() in self.early_close

    def was_market_open(self, d):
        if isinstance(d, str):
            d = date.fromisoformat(d)
        if d.weekday() >= 5:
            return False
        if d.isoformat() in self.holidays:
            return False
        return True

    def last_trading_day(self):
        d = date.today() - timedelta(days=1)
        while not self.was_market_open(d):
            d -= timedelta(days=1)
        return d

    def next_trading_day(self):
        d = date.today() + timedelta(days=1)
        while not self.was_market_open(d):
            d += timedelta(days=1)
        return d

    def trading_days_between(self, start, end):
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if isinstance(end, str):
            end = date.fromisoformat(end)
        count = 0
        d = start + timedelta(days=1)
        while d <= end:
            if self.was_market_open(d):
                count += 1
            d += timedelta(days=1)
        return count
