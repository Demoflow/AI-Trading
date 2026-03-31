"""
Intraday Entry Optimizer.
Calculates precise entry zones and waits for confirmation
triggers before executing. Runs as a continuous loop during
market hours.

Kill Zone: confluence of VWAP, prior day close, moving avg,
Fibonacci retracement, and key support levels.

Triggers: bullish reversal candle, volume spike at zone,
RSI divergence, bid/ask shift.
"""

import os
import sys
import time
import json
import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class EntryZone:
    """Represents a calculated entry zone for a candidate."""

    def __init__(self, symbol, zone_low, zone_high, ideal_price, confidence, levels):
        self.symbol = symbol
        self.zone_low = zone_low
        self.zone_high = zone_high
        self.ideal_price = ideal_price
        self.confidence = confidence
        self.levels = levels
        self.triggered = False
        self.trigger_reason = None
        self.trigger_time = None


class EntryOptimizer:

    # Only enter during these windows (ET)
    PRIMARY_START = 10  # 10:00 AM
    PRIMARY_END = 11.5  # 11:30 AM
    SECONDARY_START = 13  # 1:00 PM
    SECONDARY_END = 14.5  # 2:30 PM
    HARD_CUTOFF = 15  # 3:00 PM - never after this

    # Minimum triggers needed
    MIN_TRIGGERS = 2

    def __init__(self):
        self.active_zones = {}
        self.intraday_bars = {}

    def calculate_entry_zone(self, symbol, daily_df, watchlist_entry):
        """Calculate the optimal entry zone using level confluence."""
        if len(daily_df) < 20:
            return None

        latest = daily_df.iloc[-1]
        prev = daily_df.iloc[-2]
        price = latest["close"]

        levels = []

        # Level 1: Previous day close (magnet)
        levels.append(("prev_close", prev["close"]))

        # Level 2: VWAP approximation (typical price * volume weighted)
        tp = (latest["high"] + latest["low"] + latest["close"]) / 3
        levels.append(("vwap_approx", tp))

        # Level 3: 20-day SMA as dynamic support
        sma20 = daily_df["close"].tail(20).mean()
        levels.append(("sma_20", sma20))

        # Level 4: Fibonacci 50% retracement of last 5-day swing
        r5 = daily_df.tail(5)
        swing_high = r5["high"].max()
        swing_low = r5["low"].min()
        fib_50 = swing_high - (swing_high - swing_low) * 0.50
        fib_618 = swing_high - (swing_high - swing_low) * 0.618
        levels.append(("fib_50", fib_50))
        levels.append(("fib_618", fib_618))

        # Level 5: Prior day low as support
        levels.append(("prev_low", prev["low"]))

        # Level 6: ATR-based support zone
        atr = latest.get("atr_14", price * 0.02)
        atr_support = price - (0.5 * atr)
        levels.append(("atr_support", atr_support))

        # Find the zone: cluster of levels within 1% of each other
        level_prices = sorted([l[1] for l in levels])
        best_cluster = self._find_cluster(level_prices, price)

        if best_cluster:
            zone_low = min(best_cluster) * 0.998
            zone_high = max(best_cluster) * 1.002
            ideal = np.mean(best_cluster)
            conf = len(best_cluster) / len(levels)
        else:
            # Fallback: use VWAP +/- 0.5%
            zone_low = tp * 0.995
            zone_high = tp * 1.005
            ideal = tp
            conf = 0.3

        zone = EntryZone(
            symbol=symbol,
            zone_low=round(zone_low, 2),
            zone_high=round(zone_high, 2),
            ideal_price=round(ideal, 2),
            confidence=round(conf, 2),
            levels={name: round(val, 2) for name, val in levels}
        )

        logger.info(f"{symbol} Entry Zone: ${zone.zone_low:.2f} - ${zone.zone_high:.2f} (ideal ${zone.ideal_price:.2f}, conf {zone.confidence:.0%})")
        return zone

    def _find_cluster(self, prices, current_price):
        """Find the tightest cluster of levels below current price."""
        below = [p for p in prices if p < current_price]
        if len(below) < 2:
            return None

        best_cluster = None
        best_count = 0

        for i, anchor in enumerate(below):
            cluster = [p for p in below if abs(p - anchor) / anchor < 0.01]
            if len(cluster) > best_count:
                best_count = len(cluster)
                best_cluster = cluster

        return best_cluster if best_count >= 2 else None

    def check_triggers(self, symbol, current_price, current_volume, avg_volume, bid_size, ask_size, prev_prices):
        """
        Check if entry triggers are firing at the zone.
        prev_prices should be a list of recent 5-min closes (last 6 bars = 30 min).
        """
        zone = self.active_zones.get(symbol)
        if not zone:
            return False, []

        # Is price in the zone?
        if not (zone.zone_low <= current_price <= zone.zone_high):
            return False, []

        triggers = []

        # Trigger 1: Volume spike at zone (2x average)
        if avg_volume > 0 and current_volume > avg_volume * 2.0:
            triggers.append("volume_spike_at_zone")

        # Trigger 2: Bullish reversal (price was falling, now bouncing)
        if len(prev_prices) >= 3:
            was_falling = prev_prices[-2] < prev_prices[-3]
            now_rising = current_price > prev_prices[-1]
            if was_falling and now_rising:
                triggers.append("reversal_bounce")

        # Trigger 3: Bid/ask shift (buyers stepping in)
        if bid_size > 0 and ask_size > 0:
            if bid_size > ask_size * 1.5:
                triggers.append("bid_ask_shift")

        # Trigger 4: Price holding at zone (tested 2+ times without breaking)
        zone_tests = sum(1 for p in prev_prices if zone.zone_low <= p <= zone.zone_high)
        if zone_tests >= 2:
            triggers.append("zone_holding")

        # Trigger 5: Hammer candle (long lower wick at zone)
        if len(prev_prices) >= 2:
            bar_open = prev_prices[-2]
            bar_close = current_price
            bar_low = min(prev_prices[-1], current_price) * 0.999
            body = abs(bar_close - bar_open)
            lower_wick = min(bar_open, bar_close) - bar_low
            if lower_wick > body * 2 and bar_close > bar_open:
                triggers.append("hammer_candle")

        enough = len(triggers) >= self.MIN_TRIGGERS

        if enough:
            zone.triggered = True
            zone.trigger_reason = triggers
            zone.trigger_time = datetime.now().isoformat()
            logger.info(f"{symbol} ENTRY TRIGGERED at ${current_price:.2f} - triggers: {triggers}")

        return enough, triggers

    def is_in_entry_window(self):
        """Check if we are in an allowed entry time window."""
        now = datetime.now()
        hour_dec = now.hour + now.minute / 60.0

        if self.PRIMARY_START <= hour_dec <= self.PRIMARY_END:
            return True, "primary"
        if self.SECONDARY_START <= hour_dec <= self.SECONDARY_END:
            return True, "secondary"
        if hour_dec >= self.HARD_CUTOFF:
            return False, "past_cutoff"
        return False, "between_windows"

    def get_position_size_modifier(self, zone, triggers):
        """
        Adjust position size based on entry quality.
        V-bounce with volume = 70% immediate.
        Slow drift into zone = 50% only.
        """
        if "volume_spike_at_zone" in triggers and "reversal_bounce" in triggers:
            return 0.70  # High conviction
        elif len(triggers) >= 3:
            return 0.60  # Good conviction
        elif len(triggers) >= 2:
            return 0.50  # Minimum conviction
        return 0.40  # Low conviction

    def should_cancel(self, symbol, current_price):
        """
        Cancel entry if stock runs away without pulling back.
        If price is 3%+ above our zone, the setup is dead.
        """
        zone = self.active_zones.get(symbol)
        if not zone:
            return True

        if current_price > zone.zone_high * 1.03:
            logger.info(f"{symbol} ran 3%+ above zone - cancelling")
            return True

        in_window, window_type = self.is_in_entry_window()
        if window_type == "past_cutoff":
            logger.info(f"{symbol} past 3 PM cutoff - cancelling")
            return True

        return False
