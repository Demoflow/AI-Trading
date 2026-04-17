"""
Time Context Filter v1.
Provides sequential time-of-day awareness for 0DTE entry gating.

Replaces the binary open/closed gates with six distinct behavioral windows,
each with its own minimum confidence boost and structural restrictions.
All times are in Central Time (CT) decimal hours to match the rest of the system.
"""

from datetime import datetime
from loguru import logger


# Window definitions
# (start_ct, end_ct, name, min_conf_boost, entry_allowed, gap_aligned_only, description)
_WINDOWS = [
    (8.58,  10.0,  "opening",         0,   True,  True,
     "9:35–10:00 ET | gap + ORB only — no fades against opening direction"),
    (10.0,  10.5,  "first_pullback",  0,   True,  False,
     "10:00–11:00 ET | highest-probability window — full trending setups"),
    (10.5,  13.0,  "chop_zone",       +5,  True,  False,
     "11:30–2:00 ET | chop — +5 conf required, tighter criteria"),
    (13.0,  14.0,  "post_lunch",      0,   True,  False,
     "2:00–3:00 ET | post-lunch directional if trend established"),
    (14.0,  14.5,  "gamma_zone",      +15, True,  False,
     "3:00–3:30 ET | gamma unstable — high-delta only, +15 conf required"),
    (14.5,  15.5,  "exit_only",       0,   False, False,
     "3:30–4:30 ET | no new entries — exit only"),
]


class TimeContextFilter:

    def __init__(self):
        self._gap_direction = "FLAT"
        self._session_open_captured = False

    def capture_gap(self, first_spy_candle):
        """
        Call once after the first completed SPY 5-min candle.
        Determines opening gap direction — used to gate fades
        in the 'opening' window.
        """
        if self._session_open_captured or not first_spy_candle:
            return
        o = first_spy_candle.get("open", 0)
        c = first_spy_candle.get("close", 0)
        if o > 0 and c > 0:
            if c > o * 1.001:
                self._gap_direction = "UP"
            elif c < o * 0.999:
                self._gap_direction = "DOWN"
            else:
                self._gap_direction = "FLAT"
        self._session_open_captured = True
        logger.info(f"Gap direction captured: {self._gap_direction}")

    def get_context(self, hour_ct):
        """
        Returns the time-context dict for the current CT hour.

        Keys:
          window             - name of the current window
          entry_allowed      - bool: False means skip entry scan entirely
          min_confidence_boost - int added to MIN_CONFIDENCE threshold
          gap_aligned_only   - bool: True means signals must align with gap direction
          gap_direction      - "UP"/"DOWN"/"FLAT"
          description        - human-readable window description
        """
        for start, end, name, boost, allowed, gap_only, desc in _WINDOWS:
            if start <= hour_ct < end:
                actual_boost = boost
                # FLAT gap in opening window: price has no directional commitment.
                # Require +15 more confidence so only very high-conviction setups fire.
                if name == "opening" and self._gap_direction == "FLAT":
                    actual_boost += 15
                return {
                    "window": name,
                    "entry_allowed": allowed,
                    "min_confidence_boost": actual_boost,
                    "gap_aligned_only": gap_only,
                    "gap_direction": self._gap_direction,
                    "description": desc,
                }

        # Pre-market or post 4:30 PM CT
        return {
            "window": "closed",
            "entry_allowed": False,
            "min_confidence_boost": 0,
            "gap_aligned_only": False,
            "gap_direction": self._gap_direction,
            "description": "Outside active trading hours",
        }

    def signal_aligns_with_gap(self, signal_direction):
        """
        In the opening window, no fades against the gap direction.
        A FLAT gap allows both directions.
        """
        if self._gap_direction == "FLAT":
            return True
        if self._gap_direction == "UP" and signal_direction == "CALL":
            return True
        if self._gap_direction == "DOWN" and signal_direction == "PUT":
            return True
        return False
