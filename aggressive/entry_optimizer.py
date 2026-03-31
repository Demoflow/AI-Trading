"""
Intraday Entry Optimizer v2.
Waits for optimal entry conditions instead of batch execution.
Uses VWAP, support levels, volume, and momentum for precise timing.
"""
import time
from datetime import datetime
from loguru import logger


class EntryOptimizer:

    # Entry windows (CT)
    WINDOW_1 = (8.5, 9.25)    # First 45 min - let opening volatility settle
    WINDOW_2 = (9.25, 10.5)   # Mid-morning - best entries
    WINDOW_3 = (12.0, 13.5)   # Afternoon session
    WINDOW_4 = (13.5, 14.75)  # Power hour setup

    def __init__(self, client):
        self.client = client
        self._quote_cache = {}
        self._cache_time = {}

    def _get_quote(self, symbol):
        """Get quote with 30-second cache."""
        now = time.time()
        if symbol in self._cache_time and now - self._cache_time[symbol] < 30:
            return self._quote_cache.get(symbol, {})
        try:
            r = self.client.get_quote(symbol)
            if r.status_code == 200:
                q = r.json().get(symbol, {}).get("quote", {})
                self._quote_cache[symbol] = q
                self._cache_time[symbol] = now
                return q
        except Exception:
            pass
        return {}

    def should_enter(self, symbol, direction, option_symbol=None):
        """
        Determine if NOW is a good time to enter.
        Returns: (should_enter, limit_price, reason)
        """
        now = datetime.now()
        hour = now.hour + now.minute / 60.0

        # Don't enter in first 15 minutes (opening chaos)
        if hour < 8.75:
            return False, 0, "wait_opening"

        # Don't enter in last 30 minutes (spread widening)
        if hour > 14.75:
            return False, 0, "too_late"

        quote = self._get_quote(symbol)
        if not quote:
            return False, 0, "no_quote"

        price = quote.get("lastPrice", 0)
        vwap = quote.get("vWAP", price)
        high = quote.get("highPrice", price)
        low = quote.get("lowPrice", price)
        volume = quote.get("totalVolume", 0)
        avg_vol = quote.get("averageVolume", 1)
        change_pct = quote.get("netPercentChangeInDouble", 0)

        if price <= 0:
            return False, 0, "no_price"

        # Calculate intraday position
        day_range = high - low if high > low else 0.01
        position_in_range = (price - low) / day_range  # 0=low, 1=high

        # Volume confirmation
        vol_ratio = volume / max(avg_vol, 1)
        has_volume = vol_ratio > 0.3  # At least 30% of avg daily volume traded

        if not has_volume:
            return False, 0, "low_volume"

        # Direction-specific entry logic
        if direction == "CALL":
            # BULLISH: Enter on pullbacks, not rips
            # Best entry: price near VWAP or below, with volume
            vwap_dist = (price - vwap) / vwap * 100 if vwap > 0 else 0

            if vwap_dist < -0.3:
                # Below VWAP - great entry for calls
                return True, price, "below_vwap"
            elif vwap_dist < 0.1 and position_in_range < 0.4:
                # Near VWAP and near day's low - good pullback
                return True, price, "vwap_pullback"
            elif position_in_range < 0.3:
                # Near day's low regardless of VWAP
                return True, price, "near_low"
            elif vol_ratio > 1.5 and change_pct > 1.0:
                # Strong momentum with volume - chase is OK
                return True, price, "momentum_surge"
            elif hour > 12.0 and vwap_dist < 0.3:
                # Afternoon, near VWAP - acceptable
                return True, price, "afternoon_vwap"
            else:
                return False, 0, f"waiting_pullback_{vwap_dist:+.1f}%"

        elif direction == "PUT":
            # BEARISH: Enter on rallies, not drops
            vwap_dist = (price - vwap) / vwap * 100 if vwap > 0 else 0

            if vwap_dist > 0.3:
                # Above VWAP - great entry for puts
                return True, price, "above_vwap"
            elif vwap_dist > -0.1 and position_in_range > 0.6:
                # Near VWAP and near day's high - good rally
                return True, price, "vwap_rally"
            elif position_in_range > 0.7:
                # Near day's high
                return True, price, "near_high"
            elif vol_ratio > 1.5 and change_pct < -1.0:
                # Strong selling with volume
                return True, price, "selling_surge"
            elif hour > 12.0 and vwap_dist > -0.3:
                # Afternoon, near VWAP
                return True, price, "afternoon_vwap"
            else:
                return False, 0, f"waiting_rally_{vwap_dist:+.1f}%"

        return False, 0, "unknown_direction"

    def get_optimal_option_price(self, client, option_symbol):
        """Get the best price for an option entry."""
        try:
            r = client.get_quote(option_symbol)
            if r.status_code == 200:
                q = r.json().get(option_symbol, {}).get("quote", {})
                bid = q.get("bidPrice", 0)
                ask = q.get("askPrice", 0)
                mid = (bid + ask) / 2
                spread = ask - bid
                spread_pct = spread / mid if mid > 0 else 1.0

                # If spread is tight (<5%), enter at mid
                if spread_pct < 0.05:
                    return round(mid, 2), "tight_spread"
                # If spread is moderate (5-10%), enter slightly above mid
                elif spread_pct < 0.10:
                    return round(mid + spread * 0.2, 2), "moderate_spread"
                # If spread is wide (>10%), enter at 30% above bid
                else:
                    return round(bid + spread * 0.3, 2), "wide_spread"
        except Exception:
            pass
        return 0, "no_data"
