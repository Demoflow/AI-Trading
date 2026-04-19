"""
Shared timezone helpers — CT-aware datetime utilities.

Import from here rather than copy-pasting _now_ct()/_hour_ct() across modules.
"""

from datetime import datetime, date

try:
    from zoneinfo import ZoneInfo
    CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    CT_TZ = None


def now_ct() -> datetime:
    """Current datetime in Central Time."""
    return datetime.now(tz=CT_TZ) if CT_TZ else datetime.now()


def hour_ct() -> float:
    """Current time as decimal hours in CT (e.g. 9.5 = 9:30 AM CT)."""
    n = now_ct()
    return n.hour + n.minute / 60.0 + n.second / 3600.0


def today_ct() -> date:
    """Today's date in Central Time (not system local time)."""
    return now_ct().date()
