"""
Earnings Calendar Manager.
Fetches and caches upcoming earnings dates.
"""

import json
import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from loguru import logger

try:
    import yfinance as yf
    YF_OK = True
except ImportError:
    YF_OK = False


class EarningsCalendar:

    CACHE_PATH = "config/earnings_calendar.json"

    def __init__(self):
        self.calendar = self._load()

    def _load(self):
        if Path(self.CACHE_PATH).exists():
            with open(self.CACHE_PATH) as f:
                data = json.load(f)
            age = data.get("updated", "")
            if age:
                try:
                    ud = date.fromisoformat(age)
                    if (date.today() - ud).days <= 7:
                        return data.get("earnings", {})
                except ValueError:
                    pass
        return {}

    def _save(self):
        data = {
            "updated": date.today().isoformat(),
            "earnings": self.calendar,
        }
        Path(self.CACHE_PATH).parent.mkdir(
            parents=True, exist_ok=True
        )
        with open(self.CACHE_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def refresh(self, symbols=None):
        if not YF_OK:
            logger.warning("yfinance not available")
            return
        if symbols is None:
            symbols = []
            p = Path("config/universe.csv")
            if p.exists():
                with open(p) as f:
                    reader = csv.DictReader(f)
                    symbols = [r["symbol"] for r in reader]

        logger.info(
            f"Refreshing earnings for {len(symbols)} symbols"
        )
        for sym in symbols:
            try:
                t = yf.Ticker(sym)
                info = t.info or {}
                ed = info.get("earningsDate")
                if ed:
                    if isinstance(ed, list) and len(ed) > 0:
                        ed = ed[0]
                    if hasattr(ed, "isoformat"):
                        ed = ed.isoformat()
                    else:
                        ed = str(ed)[:10]
                    self.calendar[sym] = ed
            except Exception:
                pass

        self._save()
        logger.info(
            f"Earnings calendar updated: "
            f"{len(self.calendar)} entries"
        )

    def days_to_earnings(self, symbol):
        ed = self.calendar.get(symbol)
        if not ed:
            return 999
        try:
            earn_date = date.fromisoformat(str(ed)[:10])
            return (earn_date - date.today()).days
        except (ValueError, TypeError):
            return 999

    def is_near_earnings(self, symbol, days=5):
        d = self.days_to_earnings(symbol)
        return 0 <= d <= days

    def get_reporting_this_week(self):
        result = []
        for sym, ed in self.calendar.items():
            d = self.days_to_earnings(sym)
            if 0 <= d <= 5:
                result.append((sym, ed, d))
        result.sort(key=lambda x: x[2])
        return result
