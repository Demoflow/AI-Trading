"""
Economic Calendar v2.
- Logs warning once per scan, not per symbol
- More precise CPI/NFP date detection
- Tracks actual 2026 event dates
"""

from datetime import date, timedelta
from loguru import logger


# 2026 FOMC meeting dates (announcement day)
FOMC_2026 = [
    date(2026, 1, 29),
    date(2026, 3, 18),
    date(2026, 5, 6),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
]

# 2026 CPI release dates (typically 2nd Tues/Wed of month)
CPI_2026 = [
    date(2026, 1, 14), date(2026, 2, 12),
    date(2026, 3, 11), date(2026, 4, 14),
    date(2026, 5, 13), date(2026, 6, 10),
    date(2026, 7, 15), date(2026, 8, 12),
    date(2026, 9, 16), date(2026, 10, 14),
    date(2026, 11, 12), date(2026, 12, 9),
]

# 2026 NFP release dates (1st Friday of month)
NFP_2026 = [
    date(2026, 1, 2), date(2026, 2, 6),
    date(2026, 3, 6), date(2026, 4, 3),
    date(2026, 5, 1), date(2026, 6, 5),
    date(2026, 7, 2), date(2026, 8, 7),
    date(2026, 9, 4), date(2026, 10, 2),
    date(2026, 11, 6), date(2026, 12, 4),
]


class EconCalendar:

    def __init__(self):
        self.today = date.today()
        self._logged = False

    def _days_to_nearest(self, dates):
        best = 999
        for d in dates:
            diff = (d - self.today).days
            if 0 <= diff < best:
                best = diff
        return best

    def is_near_major_event(self):
        """Returns (near_event, event_name, days_away)"""
        fomc = self._days_to_nearest(FOMC_2026)
        if fomc <= 1:
            return True, "FOMC", fomc

        cpi = self._days_to_nearest(CPI_2026)
        if cpi <= 1:
            return True, "CPI", cpi

        nfp = self._days_to_nearest(NFP_2026)
        if nfp <= 1:
            return True, "NFP", nfp

        return False, "", 999

    def get_conviction_modifier(self):
        """Reduce conviction near major events. Logs once."""
        near, event, days = self.is_near_major_event()
        if near:
            if not self._logged:
                self._logged = True
                if days == 0:
                    logger.info(f"ECON: {event} TODAY - conviction reduced 25%")
                else:
                    logger.info(f"ECON: {event} tomorrow - conviction reduced 10%")

            if days == 0:
                return 0.75
            elif days == 1:
                return 0.90
        return 1.0
