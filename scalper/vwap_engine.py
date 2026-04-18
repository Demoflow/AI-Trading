"""
VWAP Engine v1.0 — Core VWAP calculation and signal detection for stock scalping.

Responsibilities:
  - Cumulative VWAP with 1-SD and 2-SD bands per symbol
  - Touch counting (price contacts with VWAP)
  - Signal detection: RECLAIM, REJECTION, RETEST
  - Confidence scoring (0-100) based on volume, touch count, time, candle pattern
  - Daily reset at 9:30 AM CT

Thread-safe via per-symbol locks.
"""

import math
import threading
from datetime import datetime
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


# Tier 1 symbols get a confidence boost
_TIER1 = {"SPY", "QQQ", "NVDA", "META", "AAPL"}


class VWAPEngine:
    """
    Per-symbol intraday VWAP tracker with SD bands, touch counting, and signal generation.
    """

    # Touch detection thresholds (as fraction of price)
    TOUCH_PROXIMITY = 0.0010   # Within 0.10% of VWAP = "touching"
    TOUCH_DEPARTURE = 0.0015   # Must move 0.15% away to register a completed touch

    def __init__(self):
        self._lock = threading.Lock()
        # Per-symbol state
        self._data = {}  # symbol -> dict of cumulative values

    def _init_symbol(self, symbol):
        """Initialize tracking state for a symbol."""
        return {
            "cum_pv": 0.0,         # cumulative (price * volume)
            "cum_vol": 0,          # cumulative volume
            "cum_pv_sq": 0.0,      # cumulative (price^2 * volume) for variance
            "vwap": 0.0,
            "sd": 0.0,
            "touch_count": 0,
            "in_touch_zone": False,
            "candle_history": [],   # list of recent candle dicts (last 30)
            "below_vwap_count": 0,  # consecutive candles below VWAP
            "above_vwap_count": 0,  # consecutive candles above VWAP
            "last_signal_type": None,
            "last_reclaim_time": None,
        }

    def _get(self, symbol):
        if symbol not in self._data:
            self._data[symbol] = self._init_symbol(symbol)
        return self._data[symbol]

    # ── VWAP UPDATE ──────────────────────────────────────────────────────────

    def update(self, symbol, price, volume):
        """
        Feed a new price/volume tick. Updates cumulative VWAP and bands.
        Call this each cycle for each symbol with the latest quote data.
        """
        if price <= 0 or volume <= 0:
            return
        with self._lock:
            d = self._get(symbol)
            d["cum_pv"] += price * volume
            d["cum_vol"] += volume
            d["cum_pv_sq"] += (price ** 2) * volume
            # VWAP = sum(price*vol) / sum(vol)
            d["vwap"] = d["cum_pv"] / d["cum_vol"]
            # Variance = sum(vol*(price-vwap)^2) / sum(vol)
            # Equivalent: E[P^2] - E[P]^2 weighted by volume
            mean_p_sq = d["cum_pv_sq"] / d["cum_vol"]
            variance = max(mean_p_sq - d["vwap"] ** 2, 0.0)
            d["sd"] = math.sqrt(variance)
            # Update touch tracking
            self._update_touch(d, price)

    def update_candle(self, symbol, candle):
        """
        Feed a completed candle dict with keys: open, high, low, close, volume.
        Updates VWAP using typical price and tracks candle history for signal detection.
        """
        if not candle or candle.get("volume", 0) <= 0:
            return
        tp = (candle["high"] + candle["low"] + candle["close"]) / 3.0
        vol = candle["volume"]
        with self._lock:
            d = self._get(symbol)
            d["cum_pv"] += tp * vol
            d["cum_vol"] += vol
            d["cum_pv_sq"] += (tp ** 2) * vol
            d["vwap"] = d["cum_pv"] / d["cum_vol"]
            mean_p_sq = d["cum_pv_sq"] / d["cum_vol"]
            variance = max(mean_p_sq - d["vwap"] ** 2, 0.0)
            d["sd"] = math.sqrt(variance)
            # Track candle history (keep last 30)
            d["candle_history"].append(candle)
            if len(d["candle_history"]) > 30:
                d["candle_history"] = d["candle_history"][-30:]
            # Track above/below VWAP streaks
            if candle["close"] < d["vwap"]:
                d["below_vwap_count"] += 1
                d["above_vwap_count"] = 0
            elif candle["close"] > d["vwap"]:
                d["above_vwap_count"] += 1
                d["below_vwap_count"] = 0
            else:
                d["below_vwap_count"] = 0
                d["above_vwap_count"] = 0
            # Update touch tracking with close price
            self._update_touch(d, candle["close"])

    def _update_touch(self, d, price):
        """Track touches of VWAP (price crosses within proximity then departs)."""
        vwap = d["vwap"]
        if vwap <= 0:
            return
        dist_pct = abs(price - vwap) / vwap
        if dist_pct <= self.TOUCH_PROXIMITY:
            d["in_touch_zone"] = True
        elif d["in_touch_zone"] and dist_pct >= self.TOUCH_DEPARTURE:
            d["in_touch_zone"] = False
            d["touch_count"] += 1

    # ── GETTERS ──────────────────────────────────────────────────────────────

    def get_vwap(self, symbol) -> float:
        with self._lock:
            d = self._data.get(symbol)
            return d["vwap"] if d else 0.0

    def get_bands(self, symbol):
        """Returns (vwap, sd1_upper, sd1_lower, sd2_upper, sd2_lower)."""
        with self._lock:
            d = self._data.get(symbol)
            if not d or d["vwap"] <= 0:
                return (0.0, 0.0, 0.0, 0.0, 0.0)
            v = d["vwap"]
            s = d["sd"]
            return (
                round(v, 4),
                round(v + s, 4),
                round(v - s, 4),
                round(v + 2 * s, 4),
                round(v - 2 * s, 4),
            )

    def get_touch_count(self, symbol) -> int:
        with self._lock:
            d = self._data.get(symbol)
            return d["touch_count"] if d else 0

    def get_sd(self, symbol) -> float:
        with self._lock:
            d = self._data.get(symbol)
            return d["sd"] if d else 0.0

    # ── SIGNAL DETECTION ─────────────────────────────────────────────────────

    def scan(self, symbol, candles, current_price, current_volume,
             day_type="", breadth_signal=""):
        """
        Scan for VWAP-based signals on a symbol.

        Args:
            symbol: ticker string
            candles: list of recent candle dicts (at least the last 10)
            current_price: latest price
            current_volume: current candle volume
            day_type: from DayClassifier (TRENDING, CHOPPY, etc.)
            breadth_signal: from MarketInternals (BULLISH, BEARISH, etc.)

        Returns:
            Signal dict or None.
        """
        with self._lock:
            d = self._get(symbol)
            vwap = d["vwap"]
            sd = d["sd"]
            if vwap <= 0 or not candles or len(candles) < 3:
                return None

            touch_count = d["touch_count"]
            # No signals after 3+ touches (degraded win rate)
            if touch_count > 2:
                return None

            # Compute volume ratio
            volumes = [c.get("volume", 0) for c in candles[-10:] if c.get("volume", 0) > 0]
            avg_vol = sum(volumes) / len(volumes) if volumes else 1
            vol_ratio = current_volume / avg_vol if avg_vol > 0 else 1.0

            # Candle info
            prev_candle = candles[-2] if len(candles) >= 2 else None
            prev2_candle = candles[-3] if len(candles) >= 3 else None
            cur_candle = candles[-1]

            # Check RETEST first (higher priority)
            sig = self._check_retest(symbol, d, candles, current_price, vwap, sd,
                                     touch_count, vol_ratio, day_type, breadth_signal)
            if sig:
                return sig

            # Check RECLAIM
            sig = self._check_reclaim(symbol, d, candles, current_price, vwap, sd,
                                      touch_count, vol_ratio, cur_candle, prev_candle,
                                      prev2_candle, day_type, breadth_signal)
            if sig:
                return sig

            # Check REJECTION
            sig = self._check_rejection(symbol, d, candles, current_price, vwap, sd,
                                        touch_count, vol_ratio, cur_candle, prev_candle,
                                        day_type, breadth_signal)
            if sig:
                return sig

            return None

    def _check_reclaim(self, symbol, d, candles, price, vwap, sd,
                       touch_count, vol_ratio, cur, prev, prev2, day_type, breadth):
        """VWAP RECLAIM: price was below VWAP, now closes above it with volume."""
        if not prev or not prev2:
            return None
        # Price was below VWAP for at least 2 candles
        if prev.get("close", 0) >= vwap or prev2.get("close", 0) >= vwap:
            return None
        # Current candle closes above VWAP
        if cur.get("close", 0) <= vwap:
            return None
        # Bullish candle body
        if cur.get("close", 0) <= cur.get("open", 0):
            return None
        # Volume confirmation
        if vol_ratio < 1.3:
            return None

        conf = self._score_confidence(
            touch_count, vol_ratio, symbol, day_type, breadth,
            signal_type="RECLAIM", direction="LONG"
        )
        if conf < 50:
            return None

        d["last_signal_type"] = "RECLAIM"
        d["last_reclaim_time"] = _now_ct()

        return self._build_signal(symbol, "LONG", "RECLAIM", conf, vwap, sd,
                                  price, touch_count, vol_ratio, cur)

    def _check_rejection(self, symbol, d, candles, price, vwap, sd,
                         touch_count, vol_ratio, cur, prev, day_type, breadth):
        """VWAP REJECTION: price approaches from above, wicks into VWAP, closes below."""
        if not prev:
            return None

        rejection = False
        # Pattern 1: candle wicks into VWAP but closes below
        if (prev.get("close", 0) > vwap and
                cur.get("low", 0) <= vwap * 1.001 and
                cur.get("close", 0) < vwap):
            rejection = True

        # Pattern 2: price approached VWAP from above, next candle makes lower high
        if not rejection and prev:
            prev_dist = abs(prev.get("close", 0) - vwap) / vwap if vwap > 0 else 1
            if (prev_dist <= 0.0010 and
                    prev.get("close", 0) > vwap and
                    cur.get("high", 0) < prev.get("high", 0) and
                    cur.get("close", 0) < prev.get("close", 0)):
                rejection = True

        if not rejection:
            return None

        # Volume check (high on rejection candle OR above average)
        if vol_ratio < 1.0:
            return None

        conf = self._score_confidence(
            touch_count, vol_ratio, symbol, day_type, breadth,
            signal_type="REJECTION", direction="SHORT"
        )
        if conf < 50:
            return None

        return self._build_signal(symbol, "SHORT", "REJECTION", conf, vwap, sd,
                                  price, touch_count, vol_ratio, cur)

    def _check_retest(self, symbol, d, candles, price, vwap, sd,
                      touch_count, vol_ratio, day_type, breadth):
        """
        RETEST ENTRY: price reclaimed VWAP, pulled back to within 0.08%, then bounced.
        Higher conviction than initial reclaim.
        """
        if len(candles) < 5:
            return None

        # Must have had a previous reclaim
        last_reclaim = d.get("last_reclaim_time")
        if not last_reclaim:
            return None

        # Check: recent candles show reclaim pattern followed by pullback and bounce
        # Look at last 5 candles for the sequence
        recent = candles[-5:]

        # Find a candle that closed above VWAP (reclaim), then one that pulled back close to VWAP,
        # then current bouncing away
        reclaim_found = False
        pullback_found = False
        for i, c in enumerate(recent[:-2]):
            if c.get("close", 0) > vwap:
                reclaim_found = True
            if reclaim_found and abs(c.get("close", 0) - vwap) / vwap <= 0.0008:
                pullback_found = True
                break

        if not reclaim_found or not pullback_found:
            return None

        # Current candle must be bouncing above VWAP
        cur = candles[-1]
        if cur.get("close", 0) <= vwap:
            return None
        if cur.get("close", 0) <= cur.get("open", 0):
            return None  # Must be bullish

        conf = self._score_confidence(
            touch_count, vol_ratio, symbol, day_type, breadth,
            signal_type="RETEST", direction="LONG"
        )
        # RETEST gets +15 bonus (higher conviction)
        conf += 15
        conf = min(conf, 98)

        return self._build_signal(symbol, "LONG", "RETEST", conf, vwap, sd,
                                  price, touch_count, vol_ratio, cur)

    def _score_confidence(self, touch_count, vol_ratio, symbol,
                          day_type, breadth, signal_type="", direction=""):
        """Composite confidence score 0-100."""
        conf = 50  # Base

        # Touch count
        if touch_count == 1:
            conf += 20
        elif touch_count == 2:
            conf += 10
        # touch_count == 0 means first touch — neutral

        # Volume
        if vol_ratio >= 2.0:
            conf += 15
        elif vol_ratio >= 1.5:
            conf += 10
        elif vol_ratio >= 1.3:
            conf += 5

        # Time of day
        h = _hour_ct()
        if 10.0 <= h <= 11.5:
            conf += 10  # Prime morning window
        elif 14.0 <= h <= 15.5:
            conf += 5   # Afternoon window

        # Tier 1 symbol
        if symbol in _TIER1:
            conf += 10

        # Signal type bonus
        if signal_type == "RETEST":
            conf += 10

        # Day type alignment
        if day_type == "TRENDING" and direction in ("LONG", "SHORT"):
            conf += 5

        # Breadth confirmation
        if direction == "LONG" and breadth in ("BULLISH", "STRONG_BULLISH"):
            conf += 5
        elif direction == "SHORT" and breadth in ("BEARISH", "STRONG_BEARISH"):
            conf += 5

        return min(conf, 98)

    def _build_signal(self, symbol, direction, sig_type, confidence, vwap, sd,
                      entry_price, touch_count, vol_ratio, cur_candle):
        """Build the signal dict."""
        sd1_upper = round(vwap + sd, 4)
        sd1_lower = round(vwap - sd, 4)
        sd2_upper = round(vwap + 2 * sd, 4)
        sd2_lower = round(vwap - 2 * sd, 4)

        # Detect candle pattern
        pattern = self._detect_pattern(cur_candle)

        return {
            "symbol": symbol,
            "direction": direction,
            "type": sig_type,
            "confidence": confidence,
            "vwap": round(vwap, 4),
            "sd1_upper": sd1_upper,
            "sd1_lower": sd1_lower,
            "sd2_upper": sd2_upper,
            "sd2_lower": sd2_lower,
            "entry_price": round(entry_price, 2),
            "touch_count": touch_count,
            "volume_ratio": round(vol_ratio, 2),
            "candle_pattern": pattern,
            "time": _now_ct(),
        }

    @staticmethod
    def _detect_pattern(candle):
        """Detect basic candle patterns: HAMMER, ENGULFING, DOJI, or NONE."""
        if not candle:
            return "NONE"
        o = candle.get("open", 0)
        h = candle.get("high", 0)
        lo = candle.get("low", 0)
        c = candle.get("close", 0)
        body = abs(c - o)
        full_range = h - lo
        if full_range <= 0:
            return "DOJI"
        body_ratio = body / full_range
        # Doji: tiny body
        if body_ratio < 0.1:
            return "DOJI"
        # Hammer: small body at top, long lower wick
        lower_wick = min(o, c) - lo
        if lower_wick / full_range > 0.6 and body_ratio < 0.3:
            return "HAMMER"
        # Engulfing would need previous candle — simplified here
        if body_ratio > 0.7:
            return "ENGULFING"
        return "NONE"

    # ── RESET ────────────────────────────────────────────────────────────────

    def reset_symbol(self, symbol):
        """Reset all state for a symbol (new trading day)."""
        with self._lock:
            self._data[symbol] = self._init_symbol(symbol)

    def reset_all(self):
        """Reset all symbols. Call at 9:30 AM CT for new session."""
        with self._lock:
            self._data.clear()
        logger.info("VWAPEngine: all symbols reset for new session")

    # ── SEED ─────────────────────────────────────────────────────────────────

    def seed_candles(self, symbol, candles):
        """
        Pre-populate VWAP from historical candles for mid-day restart.
        Processes each candle in order to build accurate cumulative VWAP.
        """
        with self._lock:
            d = self._init_symbol(symbol)
            for c in candles:
                vol = c.get("volume", 0)
                if vol <= 0:
                    continue
                tp = (c["high"] + c["low"] + c["close"]) / 3.0
                d["cum_pv"] += tp * vol
                d["cum_vol"] += vol
                d["cum_pv_sq"] += (tp ** 2) * vol
                d["candle_history"].append(c)
            if d["cum_vol"] > 0:
                d["vwap"] = d["cum_pv"] / d["cum_vol"]
                mean_p_sq = d["cum_pv_sq"] / d["cum_vol"]
                variance = max(mean_p_sq - d["vwap"] ** 2, 0.0)
                d["sd"] = math.sqrt(variance)
            # Trim history
            if len(d["candle_history"]) > 30:
                d["candle_history"] = d["candle_history"][-30:]
            self._data[symbol] = d
            logger.debug(
                f"VWAPEngine seeded {symbol}: VWAP={d['vwap']:.2f} "
                f"SD={d['sd']:.4f} candles={len(d['candle_history'])}"
            )
