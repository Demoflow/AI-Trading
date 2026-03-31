"""
#14: Market Regime Detector.
#15: ETF Directional Filter.
"""

import pandas as pd
from loguru import logger


class MarketRegime:

    def detect(self, spy_df):
        """
        Returns: regime, position_modifier, etf_direction
        Regimes: TRENDING_UP, CHOPPY, TRENDING_DOWN
        """
        if spy_df is None or len(spy_df) < 50:
            return "UNKNOWN", 1.0, "BOTH"

        price = spy_df.iloc[-1]["close"]
        sma20 = spy_df["close"].tail(20).mean()
        sma50 = spy_df["close"].tail(50).mean()

        # Trend direction
        above_20 = price > sma20
        above_50 = price > sma50
        sma20_rising = sma20 > spy_df["close"].tail(25).head(5).mean()
        sma50_rising = sma50 > spy_df["close"].tail(55).head(5).mean()

        # Choppiness: how many crosses of 20 SMA in last 20 days
        crosses = 0
        closes = spy_df["close"].tail(20).values
        sma_vals = spy_df["close"].rolling(20).mean().tail(20).values
        for i in range(1, len(closes)):
            if (closes[i] > sma_vals[i]) != (closes[i-1] > sma_vals[i-1]):
                crosses += 1

        if above_20 and above_50 and sma20_rising:
            if crosses <= 2:
                regime = "TRENDING_UP"
                modifier = 1.2
                etf_dir = "LONG_ONLY"
            else:
                regime = "CHOPPY_BULLISH"
                modifier = 0.9
                etf_dir = "LONG_ONLY"
        elif not above_20 and not above_50 and not sma20_rising:
            if crosses <= 2:
                regime = "TRENDING_DOWN"
                modifier = 0.5
                etf_dir = "SHORT_ONLY"
            else:
                regime = "CHOPPY_BEARISH"
                modifier = 0.6
                etf_dir = "SHORT_ONLY"
        else:
            regime = "CHOPPY"
            modifier = 0.7
            etf_dir = "NONE"

        logger.info(
            f"Market regime: {regime} "
            f"(mod={modifier}, etf={etf_dir})"
        )
        return regime, modifier, etf_dir

    def filter_etf_candidates(self, candidates, etf_dir):
        """
        Only allow ETFs that match market direction.
        """
        long_etfs = {
            "TQQQ", "UPRO", "SOXL", "LABU", "TNA", "NUGT"
        }
        short_etfs = {
            "SQQQ", "SPXU", "SOXS", "TZA"
        }
        if etf_dir == "LONG_ONLY":
            return [
                c for c in candidates
                if c["symbol"] in long_etfs
            ]
        elif etf_dir == "SHORT_ONLY":
            return [
                c for c in candidates
                if c["symbol"] in short_etfs
            ]
        elif etf_dir == "NONE":
            return []
        return candidates
