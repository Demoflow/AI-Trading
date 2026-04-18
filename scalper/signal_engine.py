"""
Signal Engine v7 — VWAP Stock Scalping.
Complete rewrite for stock positions using VWAPEngine as the core.

Scans the active symbol universe each cycle, applies time-of-day filters,
integrates VWAP signals with day classifier and market internals.
Returns the highest-conviction signal each cycle.

Tracks per-symbol cooldowns (20 min after exit) and touch counts.
"""

from datetime import datetime, timedelta
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _now_ct():
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()


def _hour_ct() -> float:
    n = _now_ct()
    return n.hour + n.minute / 60.0 + n.second / 3600.0


# Time windows
_TIME_WINDOWS = {
    "PRE_OPEN":   (0.0,  9.0),    # Before 9:00 AM CT — no trades
    "OPENING":    (9.0,  9.5),    # 9:00-9:30 AM CT — no trades (first 30 min)
    "PRIME":      (9.5,  11.5),   # 9:30-11:30 AM CT — best window
    "LUNCH":      (11.5, 13.0),   # 11:30 AM-1:00 PM CT — reduced activity
    "AFTERNOON":  (13.0, 14.5),   # 1:00-2:30 PM CT — secondary window
    "POWER_HOUR": (14.5, 15.0),   # 2:30-3:00 PM CT — last chance
    "CLOSE":      (15.0, 24.0),   # 3:00 PM+ CT — no new trades
}


def _get_time_window(h):
    for name, (start, end) in _TIME_WINDOWS.items():
        if start <= h < end:
            return name
    return "CLOSE"


class ScalperSignal:
    """
    VWAP-based signal engine for stock scalping.
    Uses VWAPEngine for signal detection, adds time/context filters.
    """

    COOLDOWN_SECONDS = 1200     # 20-minute cooldown per symbol after exit
    MIN_CONFIDENCE = 65         # Absolute floor
    LUNCH_CONFIDENCE_BOOST = 15 # Extra confidence required during lunch

    def __init__(self):
        self._cooldown = {}          # symbol -> datetime of last exit
        self._trade_date = None
        self._daily_signals = 0

    def _reset_daily(self):
        today = _now_ct().date()
        if self._trade_date != today:
            self._cooldown.clear()
            self._daily_signals = 0
            self._trade_date = today

    def record_exit(self, symbol):
        """Record that a position was exited — starts cooldown timer."""
        self._cooldown[symbol] = _now_ct()

    def _is_cooled_down(self, symbol):
        """Check if symbol cooldown has elapsed."""
        if symbol not in self._cooldown:
            return True
        elapsed = (_now_ct() - self._cooldown[symbol]).total_seconds()
        return elapsed >= self.COOLDOWN_SECONDS

    # ── MAIN SCAN ────────────────────────────────────────────────────────────

    def scan(self, vwap_engine, data_engine, stock_universe,
             day_type="", gex_regime="", vix_level=15,
             breadth=None, open_symbols=None):
        """
        Scan the active universe for the highest-conviction VWAP signal.

        Args:
            vwap_engine: VWAPEngine instance
            data_engine: RealtimeDataEngine instance
            stock_universe: StockUniverse instance
            day_type: from DayClassifier
            gex_regime: from IntradayGEX
            vix_level: current VIX
            breadth: dict from MarketInternals.get_breadth()
            open_symbols: set of symbols with open positions (skip these)

        Returns:
            Signal dict or None.
        """
        self._reset_daily()
        h = _hour_ct()
        window = _get_time_window(h)

        # No trades during opening, pre-open, or close
        if window in ("PRE_OPEN", "OPENING", "CLOSE"):
            return None

        # Get active symbols for current conditions
        active = stock_universe.get_active_symbols(day_type, gex_regime, vix_level)
        if not active:
            return None

        open_syms = open_symbols or set()
        breadth_signal = breadth.get("signal", "MIXED") if breadth else "MIXED"

        candidates = []

        for symbol in active:
            # Skip symbols with open positions
            if symbol in open_syms:
                continue

            # Skip symbols on cooldown
            if not self._is_cooled_down(symbol):
                continue

            # Get candle data
            snap = data_engine.get_snapshot(symbol)
            if not snap:
                continue

            # Use proxy VWAP for leveraged ETFs
            vwap_symbol = stock_universe.get_vwap_proxy(symbol)

            # Get candles for signal detection
            builder = data_engine.builders_5m.get(vwap_symbol)
            if not builder:
                continue
            candles = builder.get_all_candles()
            if not candles or len(candles) < 5:
                continue

            current_price = snap["price"]
            current_candle = snap.get("current_candle", {})
            current_volume = current_candle.get("volume", 0) if current_candle else 0

            # For proxy symbols, use the proxy's price for VWAP comparison
            # but keep the actual trading symbol's price for entry
            scan_price = current_price
            if vwap_symbol != symbol:
                proxy_snap = data_engine.get_snapshot(vwap_symbol)
                if proxy_snap:
                    scan_price = proxy_snap["price"]
                else:
                    continue  # Can't compare against proxy VWAP without proxy price

            # Scan VWAPEngine for signal on the VWAP symbol
            signal = vwap_engine.scan(
                vwap_symbol, candles, scan_price, current_volume,
                day_type=day_type, breadth_signal=breadth_signal
            )

            if not signal:
                continue

            # Override symbol and entry price to the actual trading symbol
            signal["symbol"] = symbol
            signal["entry_price"] = round(current_price, 2)
            signal["_is_proxy"] = (vwap_symbol != symbol)

            # Apply time window adjustments
            signal["time_window"] = window
            if window == "LUNCH":
                signal["confidence"] -= self.LUNCH_CONFIDENCE_BOOST
            elif window == "POWER_HOUR":
                signal["confidence"] -= 5  # Slight reduction for late-day

            # Check minimum confidence (per-symbol)
            min_conf = stock_universe.get_min_confidence(symbol)
            if signal["confidence"] < min_conf:
                continue

            # Compute stop and target prices
            stop_dist_pct = stock_universe.get_stop_distance_pct(symbol)
            signal = self._compute_levels(signal, stop_dist_pct)

            # Score for priority ranking
            priority = stock_universe.score_symbol(symbol, day_type, gex_regime)
            signal["priority_score"] = priority + signal["confidence"]

            candidates.append(signal)

        if not candidates:
            return None

        # Return highest conviction signal
        candidates.sort(key=lambda s: s["priority_score"], reverse=True)
        best = candidates[0]

        self._daily_signals += 1
        logger.info(
            f"SIGNAL: {best['type']} {best['direction']} {best['symbol']} "
            f"conf:{best['confidence']} vwap:{best['vwap']:.2f} "
            f"vol:{best['volume_ratio']:.1f}x touch:{best['touch_count']} "
            f"[{best['time_window']}] {best['candle_pattern']}"
        )

        return best

    def _compute_levels(self, signal, stop_dist_pct):
        """Compute stop_price, target_1, target_2 from signal data."""
        price = signal["entry_price"]
        direction = signal["direction"]
        is_proxy = signal.get("_is_proxy", False)

        if is_proxy:
            # Proxy symbols (e.g., TQQQ using QQQ VWAP): compute stops and targets
            # purely from the trading symbol's price, since VWAP/SD bands are in
            # the proxy's price space and cannot be used directly.
            if direction == "LONG":
                stop_price = round(price * (1 - stop_dist_pct / 100), 2)
                target_1 = round(price * (1 + stop_dist_pct / 100 * 1.5), 2)
                target_2 = round(price * (1 + stop_dist_pct / 100 * 3.0), 2)
            else:
                stop_price = round(price * (1 + stop_dist_pct / 100), 2)
                target_1 = round(price * (1 - stop_dist_pct / 100 * 1.5), 2)
                target_2 = round(price * (1 - stop_dist_pct / 100 * 3.0), 2)
        else:
            vwap = signal["vwap"]
            if direction == "LONG":
                # Stop below VWAP minus stop distance
                stop_price = round(vwap * (1 - stop_dist_pct / 100), 2)
                # Also ensure stop is below entry
                stop_price = min(stop_price, round(price * (1 - stop_dist_pct / 100), 2))
                target_1 = signal["sd1_upper"]
                target_2 = signal["sd2_upper"]
            else:  # SHORT
                stop_price = round(vwap * (1 + stop_dist_pct / 100), 2)
                stop_price = max(stop_price, round(price * (1 + stop_dist_pct / 100), 2))
                target_1 = signal["sd1_lower"]
                target_2 = signal["sd2_lower"]

        signal["stop_price"] = stop_price
        signal["target_1"] = round(target_1, 2)
        signal["target_2"] = round(target_2, 2)

        return signal
