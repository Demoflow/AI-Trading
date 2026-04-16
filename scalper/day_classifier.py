"""
Day Type Classifier v2 — Regime Transition Detection.
- Initial classification after first 30 min (unchanged)
- Rolling regime_history[] buffer (last 5 snapshots, updated every 30 min)
- Transition detection: RANGE→TRENDING, TRENDING→VOLATILE, etc.
- Transition governs live strategy selection overrides in scalper_live.py
"""

from collections import deque
from datetime import datetime
from loguru import logger


# Canonical transition types and their strategy implications
TRANSITION_IMPLICATIONS = {
    "RANGE_TO_TRENDING":   "directional_bias",    # Go directional immediately
    "TRENDING_TO_VOLATILE":"stand_aside",          # Trend losing structure — reduce/stop
    "VOLATILE_TO_RANGE":   "mean_reversion",       # Vol compression — fade extremes
    "QUIET_TO_EXPANSION":  "reassess",             # ATR spike on quiet day — stop all
    "CHOPPY_TO_TRENDING":  "directional_bias",
    "TRENDING_TO_RANGE":   "mean_reversion",
}


class DayClassifier:

    def __init__(self):
        self.day_type = "UNKNOWN"
        self.classified = False
        self._opening_range = None
        self._morning_trend = None
        # Rolling history: deque of (timestamp, day_type, atr) — last 5 snapshots
        self._regime_history = deque(maxlen=5)
        self._last_transition = None
        self._last_atr = 0.0

    # ── INITIAL CLASSIFICATION ─────────────────────────────────────────────────

    def classify(self, candle_builder, vix_level=20, atr=None):
        """
        Classify day type from first 15-30 min of data.
        Requires at least 3-6 completed 5-min candles.
        atr: optional current ATR for QUIET detection
        """
        candles = list(candle_builder.candles)
        if len(candles) < 3:
            return "UNKNOWN"

        or_high = max(c["high"] for c in candles[:3])
        or_low  = min(c["low"]  for c in candles[:3])
        or_range = or_high - or_low
        mid_price = (or_high + or_low) / 2

        self._opening_range = {
            "high": or_high,
            "low":  or_low,
            "range": or_range,
            "range_pct": or_range / mid_price * 100,
        }

        if len(candles) >= 6:
            recent_high  = max(c["high"] for c in candles[3:6])
            recent_low   = min(c["low"]  for c in candles[3:6])
            recent_close = candles[-1]["close"]

            broke_up   = recent_high > or_high * 1.001
            broke_down = recent_low  < or_low  * 0.999
            stayed_in  = not broke_up and not broke_down

            up_candles   = sum(1 for c in candles if c["close"] > c["open"])
            down_candles = sum(1 for c in candles if c["close"] < c["open"])
            direction_ratio = max(up_candles, down_candles) / len(candles)

            if stayed_in and or_range / mid_price < 0.003:
                # Very tight — QUIET day (low ATR confirms)
                self.day_type = "QUIET"
            elif stayed_in and or_range / mid_price < 0.005:
                self.day_type = "RANGE_BOUND"
            elif (broke_up or broke_down) and direction_ratio > 0.7:
                self.day_type = "TRENDING"
            elif or_range / mid_price > 0.01:
                self.day_type = "VOLATILE"
            elif stayed_in:
                self.day_type = "RANGE_BOUND"
            else:
                self.day_type = "CHOPPY"

            # VIX override
            if vix_level > 30 and self.day_type in ("RANGE_BOUND", "QUIET"):
                self.day_type = "VOLATILE"

            self.classified = True

            if recent_close > or_high:
                self._morning_trend = "BULLISH"
            elif recent_close < or_low:
                self._morning_trend = "BEARISH"
            else:
                self._morning_trend = "NEUTRAL"

            if atr:
                self._last_atr = atr

            # Seed history with initial classification
            self._regime_history.append({
                "time": datetime.now(),
                "day_type": self.day_type,
                "atr": atr or 0,
            })

            logger.info(
                f"Day Type: {self.day_type} | "
                f"OR: ${or_low:.2f}–${or_high:.2f} "
                f"({self._opening_range['range_pct']:.2f}%) | "
                f"Trend: {self._morning_trend} | VIX: {vix_level:.1f}"
            )

        return self.day_type

    # ── REGIME UPDATE (called every 30 min from main loop) ────────────────────

    def update_regime(self, candle_builder, vix_level=20, atr=None):
        """
        Re-classify based on the full session candles so far.
        Append snapshot to regime_history. Detect transitions.
        Call every 30 min (360 cycles × 5s).
        """
        if not candle_builder or candle_builder.candle_count() < 6:
            return self.day_type

        candles = list(candle_builder.candles)
        if len(candles) < 6:
            return self.day_type

        # Re-classify using all available candles
        or_high  = max(c["high"] for c in candles[:3])
        or_low   = min(c["low"]  for c in candles[:3])
        mid_price = (or_high + or_low) / 2

        recent  = candles[-6:]
        r_high  = max(c["high"] for c in recent)
        r_low   = min(c["low"]  for c in recent)
        r_close = candles[-1]["close"]

        broke_up   = r_high > or_high * 1.001
        broke_down = r_low  < or_low  * 0.999
        stayed_in  = not broke_up and not broke_down

        up_c   = sum(1 for c in recent if c["close"] > c["open"])
        dn_c   = sum(1 for c in recent if c["close"] < c["open"])
        dratio = max(up_c, dn_c) / len(recent)

        atr_val = atr or self._last_atr

        or_range = or_high - or_low
        if stayed_in and or_range / mid_price < 0.003:
            new_type = "QUIET"
        elif stayed_in and or_range / mid_price < 0.005:
            new_type = "RANGE_BOUND"
        elif (broke_up or broke_down) and dratio > 0.65:
            new_type = "TRENDING"
        elif or_range / mid_price > 0.01 or (atr_val > self._last_atr * 1.3 and self._last_atr > 0):
            new_type = "VOLATILE"
        elif stayed_in:
            new_type = "RANGE_BOUND"
        else:
            new_type = "CHOPPY"

        if vix_level > 30 and new_type in ("RANGE_BOUND", "QUIET"):
            new_type = "VOLATILE"

        prev_type = self.day_type
        self.day_type = new_type
        if atr:
            self._last_atr = atr

        snapshot = {
            "time": datetime.now(),
            "day_type": new_type,
            "atr": atr_val,
        }
        self._regime_history.append(snapshot)

        # Detect transition
        transition = self._detect_transition(prev_type, new_type, atr_val)
        if transition:
            self._last_transition = transition
            implication = TRANSITION_IMPLICATIONS.get(transition, "")
            logger.warning(
                f"REGIME TRANSITION: {prev_type} → {new_type} "
                f"[{transition}] → {implication}"
            )
        elif prev_type != new_type:
            logger.info(f"Regime updated: {prev_type} → {new_type}")
        else:
            logger.debug(f"Regime stable: {new_type}")

        return new_type

    # ── TRANSITION DETECTION ──────────────────────────────────────────────────

    def _detect_transition(self, prev, current, atr):
        """Map prev→current regime pair to a named transition."""
        if prev == current:
            return None

        key = f"{prev}_TO_{current}"
        # Direct named transitions
        if key in TRANSITION_IMPLICATIONS:
            return key

        # Special case: QUIET → anything is always an expansion alert
        if prev == "QUIET" and current != "QUIET":
            return "QUIET_TO_EXPANSION"

        # Any → TRENDING after range/chop
        if prev in ("RANGE_BOUND", "CHOPPY", "QUIET") and current == "TRENDING":
            return "RANGE_TO_TRENDING"

        # Any → VOLATILE signals breakdown of prior structure
        if current == "VOLATILE" and prev in ("TRENDING", "RANGE_BOUND"):
            return f"{prev}_TO_VOLATILE"

        return None

    def get_regime_transition(self):
        """Return the most recently detected transition, or None."""
        return self._last_transition

    def get_transition_implication(self):
        """Return strategy implication of the last transition."""
        if not self._last_transition:
            return None
        return TRANSITION_IMPLICATIONS.get(self._last_transition)

    def clear_transition(self):
        """Call after acting on a transition to prevent re-triggering."""
        self._last_transition = None

    def get_regime_history(self):
        return list(self._regime_history)

    # ── STRATEGY WINDOW SELECTION ─────────────────────────────────────────────

    def get_strategy_for_window(self, hour_ct):
        """
        Returns valid long-option strategies for the current time window and day type.
        Regime transitions can promote or suppress strategies in real time.
        """
        strategies = []
        implication = self.get_transition_implication()

        # If transition says stand_aside, only allow the most conservative entries
        stand_aside = (implication == "stand_aside")

        # Morning session (8:35–11:00 ET / 8:58–10:5 CT)
        if 8.58 <= hour_ct < 10.5:
            if stand_aside:
                strategies.append("VWAP_PULLBACK")
            elif self.day_type in ("TRENDING", "VOLATILE"):
                strategies.extend(["ORB_BREAKOUT", "EMA_MOMENTUM", "VWAP_PULLBACK"])
            elif self.day_type == "RANGE_BOUND":
                strategies.extend(["VWAP_PULLBACK", "MOMENTUM_FADE"])
            elif self.day_type == "QUIET":
                strategies.append("VWAP_PULLBACK")
            else:  # CHOPPY / UNKNOWN
                strategies.extend(["VWAP_PULLBACK", "EMA_MOMENTUM"])

        # Afternoon session (2:00–3:30 ET / 13.0–14.5 CT)
        if 13.0 <= hour_ct < 14.5:
            if stand_aside:
                strategies.append("VWAP_PULLBACK")
            else:
                strategies.extend(["VWAP_PULLBACK", "EMA_MOMENTUM", "MOMENTUM_FADE"])
                if self.day_type in ("TRENDING", "VOLATILE") or implication == "directional_bias":
                    strategies.append("DIRECTIONAL_BUY")

        # Power hour (3:30–4:00 ET / 14.5–15.0 CT)
        if 14.5 <= hour_ct < 15.0:
            if not stand_aside:
                strategies.extend(["VWAP_PULLBACK", "MOMENTUM_FADE"])
                if self.day_type == "TRENDING" or implication == "directional_bias":
                    strategies.append("EMA_MOMENTUM")

        return strategies

    def get_opening_range(self):
        return self._opening_range

    def get_morning_trend(self):
        return self._morning_trend
