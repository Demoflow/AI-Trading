"""
Day Type Classifier.
Analyzes first 15-30 min to determine market character.
Selects optimal strategy for each time window.
"""

from datetime import datetime
from loguru import logger


class DayClassifier:

    def __init__(self):
        self.day_type = "UNKNOWN"
        self.classified = False
        self._opening_range = None
        self._morning_trend = None

    def classify(self, candle_builder, vix_level=20):
        """
        Classify day type from first 15-30 min of data.
        Requires at least 3-6 completed 5-min candles.
        """
        candles = list(candle_builder.candles)
        if len(candles) < 3:
            return "UNKNOWN"

        # Opening range = first 3 candles (15 min)
        or_high = max(c["high"] for c in candles[:3])
        or_low = min(c["low"] for c in candles[:3])
        or_range = or_high - or_low
        mid_price = (or_high + or_low) / 2

        self._opening_range = {
            "high": or_high,
            "low": or_low,
            "range": or_range,
            "range_pct": or_range / mid_price * 100,
        }

        # Check if price broke out of opening range
        if len(candles) >= 6:
            recent_high = max(c["high"] for c in candles[3:6])
            recent_low = min(c["low"] for c in candles[3:6])
            recent_close = candles[-1]["close"]

            broke_up = recent_high > or_high * 1.001
            broke_down = recent_low < or_low * 0.999
            stayed_in = not broke_up and not broke_down

            # Measure directional consistency
            up_candles = sum(
                1 for c in candles if c["close"] > c["open"]
            )
            down_candles = sum(
                1 for c in candles if c["close"] < c["open"]
            )
            direction_ratio = max(up_candles, down_candles) / len(candles)

            # Classify
            if stayed_in and or_range / mid_price < 0.005:
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
            if vix_level > 30:
                if self.day_type == "RANGE_BOUND":
                    self.day_type = "VOLATILE"

            self.classified = True

            # Determine morning trend
            if recent_close > or_high:
                self._morning_trend = "BULLISH"
            elif recent_close < or_low:
                self._morning_trend = "BEARISH"
            else:
                self._morning_trend = "NEUTRAL"

            logger.info(
                f"Day Type: {self.day_type} | "
                f"OR: ${or_low:.2f}-${or_high:.2f} "
                f"({self._opening_range['range_pct']:.2f}%) | "
                f"Trend: {self._morning_trend} | "
                f"VIX: {vix_level:.1f}"
            )

        return self.day_type

    def get_strategy_for_window(self, hour_ct):
        """
        Returns which strategy templates are valid
        for the current time window and day type.
        CT = Central Time
        """
        strategies = []

        # Morning session (8:30-10:30 CT = 9:30-11:30 ET)
        if 8.5 <= hour_ct < 10.5:
            if self.day_type in ("TRENDING", "VOLATILE"):
                strategies.append("DIRECTIONAL_BUY")
                strategies.append("ORB_BREAKOUT")
            if self.day_type == "RANGE_BOUND":
                strategies.append("CREDIT_SPREAD")
                strategies.append("IRON_CONDOR")
                strategies.append("NAKED_PUT")
                strategies.append("STRANGLE_SELL")
            if self.day_type in ("CHOPPY", "UNKNOWN"):
                strategies.append("VWAP_PULLBACK")

        # Lunch (10:30-1:00 CT = 11:30-2:00 ET) - NO TRADING
        # Already handled by risk manager

        # Afternoon session (1:00-2:30 CT = 2:00-3:30 ET)
        if 13.0 <= hour_ct < 14.5:
            if self.day_type in ("TRENDING",):
                strategies.append("DIRECTIONAL_BUY")
                strategies.append("VWAP_PULLBACK")
            if self.day_type in ("RANGE_BOUND", "CHOPPY"):
                strategies.append("CREDIT_SPREAD")
                strategies.append("IRON_CONDOR")
                strategies.append("NAKED_PUT")
                strategies.append("STRANGLE_SELL")
            # Theta acceleration starts - sellers have edge
            strategies.append("PREMIUM_SELL")
            strategies.append("STRADDLE_SELL")
            strategies.append("NAKED_PUT")
            strategies.append("NAKED_CALL")
            strategies.append("STRANGLE_SELL")
            strategies.append("RATIO_SPREAD")

        # Power hour (2:30-3:00 CT = 3:30-4:00 ET)
        if 14.5 <= hour_ct < 15.0:
            # Gamma explosion - only for experienced
            if self.day_type == "TRENDING":
                strategies.append("DIRECTIONAL_BUY")
            # Premium selling has max theta advantage
            strategies.append("PREMIUM_SELL")
            strategies.append("NAKED_PUT")
            strategies.append("NAKED_CALL")
            strategies.append("STRANGLE_SELL")
            strategies.append("RATIO_SPREAD")

        return strategies

    def get_opening_range(self):
        return self._opening_range

    def get_morning_trend(self):
        return self._morning_trend
