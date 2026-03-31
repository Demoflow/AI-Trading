"""
#12 - Sector Rotation Detector.
Ranks sectors by relative strength over 2 weeks.
"""

import pandas as pd
from loguru import logger


class SectorRotation:

    SECTOR_ETFS = {
        "Technology": "XLK",
        "Healthcare": "XLV",
        "Financials": "XLF",
        "Consumer Discretionary": "XLY",
        "Consumer Staples": "XLP",
        "Energy": "XLE",
        "Industrials": "XLI",
        "Materials": "XLB",
    }

    def rank_sectors(self, sector_dfs, spy_df, lookback=10):
        """
        Rank sectors by relative strength vs SPY.
        Returns dict: sector -> (rank, modifier).
        Top 2 get +10% boost, bottom 2 get -10%.
        """
        if spy_df is None or len(spy_df) < lookback:
            return {}

        spy_ret = (
            spy_df["close"].iloc[-1]
            / spy_df["close"].iloc[-lookback] - 1
        )

        scores = {}
        for sector, etf in self.SECTOR_ETFS.items():
            df = sector_dfs.get(sector)
            if df is None or len(df) < lookback:
                scores[sector] = 0
                continue
            sec_ret = (
                df["close"].iloc[-1]
                / df["close"].iloc[-lookback] - 1
            )
            rs = sec_ret - spy_ret
            scores[sector] = round(rs, 4)

        ranked = sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        result = {}
        for i, (sector, rs) in enumerate(ranked):
            if i < 2:
                result[sector] = {
                    "rank": i + 1,
                    "rs": rs,
                    "modifier": 1.10,
                    "label": "LEADING",
                }
            elif i >= len(ranked) - 2:
                result[sector] = {
                    "rank": i + 1,
                    "rs": rs,
                    "modifier": 0.90,
                    "label": "LAGGING",
                }
            else:
                result[sector] = {
                    "rank": i + 1,
                    "rs": rs,
                    "modifier": 1.0,
                    "label": "NEUTRAL",
                }

        return result
