"""
Market Internals v2 — Breadth Divergence Detection.
Tracks 11 sector ETFs to approximate NYSE breadth.

v2 additions:
  - _breadth_history: rolling deque of last 10 readings with SPY price
  - record_breadth(breadth, spy_price): append after each get_breadth() call
  - get_divergence(): slope of breadth vs slope of SPY over last 4 readings
    Returns divergence score -100 to +100:
      Positive: accumulation (price flat/down but breadth improving)
      Negative: distribution (price rising but breadth deteriorating)
  - Divergence feeds into signal_engine.scan() as a confidence modifier
    and a direction override for high-divergence scenarios
"""

import httpx
from collections import deque
from datetime import datetime
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _now_ct():
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()

SECTOR_ETFS = [
    "XLF",   # Financials
    "XLK",   # Technology
    "XLE",   # Energy
    "XLV",   # Healthcare
    "XLC",   # Communication
    "XLI",   # Industrials
    "XLP",   # Consumer Staples
    "XLRE",  # Real Estate
    "XLB",   # Materials
    "XLU",   # Utilities
    "XLY",   # Consumer Discretionary
]


class MarketInternals:

    def __init__(self, schwab_client):
        self.client = schwab_client
        self._baseline = {}
        self._initialized = False
        # Rolling breadth history: {"time", "breadth_pct", "spy_price", "signal"}
        self._breadth_history = deque(maxlen=10)

    def initialize(self):
        """Capture opening prices for breadth calculation (single batch request)."""
        try:
            resp = self.client.get_quotes(SECTOR_ETFS)
            if resp.status_code == httpx.codes.OK:
                data = resp.json()
                for etf in SECTOR_ETFS:
                    q = data.get(etf, {}).get("quote", {})
                    op = q.get("openPrice", 0)
                    if op > 0:
                        self._baseline[etf] = op
        except Exception as e:
            logger.debug(f"Market internals init error: {e}")
        self._initialized = len(self._baseline) >= 8
        if self._initialized:
            logger.info(f"Market internals: tracking {len(self._baseline)} sectors")

    # ── BREADTH SNAPSHOT ──────────────────────────────────────────────────────

    def get_breadth(self):
        """
        Calculate current market breadth.
        Returns dict with breadth metrics.
        """
        if not self._initialized:
            self.initialize()
            if not self._initialized:
                return None

        advancing    = 0
        declining    = 0
        total_change = 0.0

        try:
            symbols = list(self._baseline.keys())
            resp = self.client.get_quotes(symbols)
            batch = resp.json() if resp.status_code == httpx.codes.OK else {}
        except Exception as e:
            logger.debug(f"Market internals breadth fetch error: {e}")
            batch = {}

        for etf, open_price in self._baseline.items():
            try:
                q = batch.get(etf, {}).get("quote", {})
                current = q.get("lastPrice", 0)
                if current > 0 and open_price > 0:
                    change = (current - open_price) / open_price
                    total_change += change
                    if change > 0.001:
                        advancing += 1
                    elif change < -0.001:
                        declining += 1
            except Exception:
                pass

        total = advancing + declining
        if total == 0:
            return None

        breadth_pct = advancing / len(self._baseline)
        avg_change  = total_change / len(self._baseline) * 100

        if breadth_pct >= 0.72:
            signal = "STRONG_BULLISH"
        elif breadth_pct >= 0.55:
            signal = "BULLISH"
        elif breadth_pct <= 0.27:
            signal = "STRONG_BEARISH"
        elif breadth_pct <= 0.45:
            signal = "BEARISH"
        else:
            signal = "MIXED"

        return {
            "advancing":   advancing,
            "declining":   declining,
            "breadth_pct": round(breadth_pct, 2),
            "avg_change":  round(avg_change, 3),
            "signal":      signal,
        }

    # ── BREADTH HISTORY ───────────────────────────────────────────────────────

    def record_breadth(self, breadth, spy_price):
        """
        Call after every get_breadth() to build the divergence history.
        breadth: dict from get_breadth()
        spy_price: current SPY last price
        """
        if not breadth or spy_price <= 0:
            return
        self._breadth_history.append({
            "time":       _now_ct(),
            "breadth_pct": breadth["breadth_pct"],
            "signal":      breadth["signal"],
            "spy_price":   round(spy_price, 2),
        })

    # ── DIVERGENCE DETECTION ──────────────────────────────────────────────────

    def get_divergence(self):
        """
        Compare the slope of breadth_pct vs the slope of SPY price
        over the last 4 readings.

        Returns dict:
          score        -100 to +100
                       Positive = accumulation (breadth > price slope)
                       Negative = distribution (price > breadth slope)
          type         "ACCUMULATION" / "DISTRIBUTION" / "ALIGNED_BULL" /
                       "ALIGNED_BEAR" / "NEUTRAL"
          description  human-readable
          signal_bias  "CALL" / "PUT" / "NEUTRAL"
          n_readings   how many readings used
        """
        h = list(self._breadth_history)
        n = min(len(h), 4)
        if n < 3:
            return self._no_divergence(n)

        recent = h[-n:]

        # Compute slopes via simple rise/run between first and last readings
        b_first = recent[0]["breadth_pct"]
        b_last  = recent[-1]["breadth_pct"]
        p_first = recent[0]["spy_price"]
        p_last  = recent[-1]["spy_price"]

        if b_first == 0 or p_first == 0:
            return self._no_divergence(n)

        b_slope = (b_last - b_first) / b_first   # % change in breadth ratio
        p_slope = (p_last - p_first) / p_first   # % change in SPY price

        # Detect sequences of sustained deterioration/improvement
        b_falling_streak = sum(
            1 for i in range(1, len(recent))
            if recent[i]["breadth_pct"] < recent[i-1]["breadth_pct"]
        )
        b_rising_streak  = sum(
            1 for i in range(1, len(recent))
            if recent[i]["breadth_pct"] > recent[i-1]["breadth_pct"]
        )
        p_rising_streak  = sum(
            1 for i in range(1, len(recent))
            if recent[i]["spy_price"] > recent[i-1]["spy_price"]
        )
        p_falling_streak = sum(
            1 for i in range(1, len(recent))
            if recent[i]["spy_price"] < recent[i-1]["spy_price"]
        )

        streak_len = n - 1  # max streak

        # Distribution: price rising while breadth deteriorating 3+ readings
        if p_rising_streak >= streak_len and b_falling_streak >= streak_len:
            score = -80
            return {
                "score": score,
                "type": "DISTRIBUTION",
                "description": (
                    f"Price rising {p_slope:+.3%} but breadth deteriorating "
                    f"{b_slope:+.3%} over {n} readings — distribution"
                ),
                "signal_bias": "PUT",
                "n_readings": n,
            }

        # Accumulation: price flat/down while breadth improving 3+ readings
        if b_rising_streak >= streak_len and p_falling_streak >= streak_len - 1:
            score = +80
            return {
                "score": score,
                "type": "ACCUMULATION",
                "description": (
                    f"Price {p_slope:+.3%} but breadth improving "
                    f"{b_slope:+.3%} over {n} readings — accumulation"
                ),
                "signal_bias": "CALL",
                "n_readings": n,
            }

        # Fake breakout: spike in breadth then immediate collapse
        if (len(recent) >= 3 and
                recent[-2]["breadth_pct"] > recent[-3]["breadth_pct"] + 0.10 and
                recent[-1]["breadth_pct"] < recent[-2]["breadth_pct"] - 0.10):
            score = -50
            return {
                "score": score,
                "type": "FAKE_BREADTH_SPIKE",
                "description": "Breadth spike then immediate collapse — fake breakout signal",
                "signal_bias": "PUT",
                "n_readings": n,
            }

        # Aligned trending: both rising
        if p_slope > 0.002 and b_slope > 0.05:
            score = +40
            return {
                "score": score,
                "type": "ALIGNED_BULL",
                "description": f"Price and breadth both rising — genuine trend",
                "signal_bias": "CALL",
                "n_readings": n,
            }

        # Aligned declining: both falling
        if p_slope < -0.002 and b_slope < -0.05:
            score = -40
            return {
                "score": score,
                "type": "ALIGNED_BEAR",
                "description": f"Price and breadth both falling — genuine downtrend",
                "signal_bias": "PUT",
                "n_readings": n,
            }

        return self._no_divergence(n)

    @staticmethod
    def _no_divergence(n):
        return {
            "score":       0,
            "type":        "NEUTRAL",
            "description": f"No significant divergence ({n} readings)",
            "signal_bias": "NEUTRAL",
            "n_readings":  n,
        }

    # ── DIRECTION CONFIRMATION ────────────────────────────────────────────────

    def confirms_direction(self, direction, breadth=None):
        """Check if breadth confirms the trade direction."""
        if breadth is None:
            breadth = self.get_breadth()
        if breadth is None:
            return True, "no_breadth_data"

        if direction == "CALL":
            if breadth["signal"] in ("STRONG_BULLISH", "BULLISH"):
                return True, f"breadth_confirms_{breadth['advancing']}/{len(self._baseline)}"
            if breadth["signal"] in ("STRONG_BEARISH", "BEARISH"):
                return False, f"breadth_diverges_{breadth['advancing']}/{len(self._baseline)}"
        elif direction == "PUT":
            if breadth["signal"] in ("STRONG_BEARISH", "BEARISH"):
                return True, f"breadth_confirms_{breadth['declining']}/{len(self._baseline)}"
            if breadth["signal"] in ("STRONG_BULLISH", "BULLISH"):
                return False, f"breadth_diverges_{breadth['declining']}/{len(self._baseline)}"

        return True, "breadth_neutral"
