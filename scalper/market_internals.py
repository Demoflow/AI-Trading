"""
Market Internals Proxy.
Tracks 11 sector ETFs to approximate NYSE breadth.
Confirms or warns against directional signals.
"""

import httpx
from loguru import logger

SECTOR_ETFS = [
    "XLF",  # Financials
    "XLK",  # Technology
    "XLE",  # Energy
    "XLV",  # Healthcare
    "XLC",  # Communication
    "XLI",  # Industrials
    "XLP",  # Consumer Staples
    "XLRE", # Real Estate
    "XLB",  # Materials
    "XLU",  # Utilities
    "XLY",  # Consumer Discretionary
]


class MarketInternals:

    def __init__(self, schwab_client):
        self.client = schwab_client
        self._baseline = {}
        self._initialized = False

    def initialize(self):
        """Capture opening prices for breadth calculation."""
        for etf in SECTOR_ETFS:
            try:
                resp = self.client.get_quote(etf)
                if resp.status_code == httpx.codes.OK:
                    data = resp.json()
                    q = data.get(etf, {}).get("quote", {})
                    op = q.get("openPrice", 0)
                    if op > 0:
                        self._baseline[etf] = op
            except Exception:
                pass
        self._initialized = len(self._baseline) >= 8
        if self._initialized:
            logger.info(f"Market internals: tracking {len(self._baseline)} sectors")

    def get_breadth(self):
        """
        Calculate current market breadth.
        Returns dict with breadth metrics.
        """
        if not self._initialized:
            self.initialize()
            if not self._initialized:
                return None

        advancing = 0
        declining = 0
        total_change = 0

        for etf, open_price in self._baseline.items():
            try:
                resp = self.client.get_quote(etf)
                if resp.status_code == httpx.codes.OK:
                    data = resp.json()
                    q = data.get(etf, {}).get("quote", {})
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
        avg_change = total_change / len(self._baseline) * 100

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
            "advancing": advancing,
            "declining": declining,
            "breadth_pct": round(breadth_pct, 2),
            "avg_change": round(avg_change, 3),
            "signal": signal,
        }

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
