"""
Technical Momentum Scoring (30% weight).
STRATEGY: Buy pullbacks within confirmed uptrends.
NOT momentum chasing - we want temporary weakness
in strong stocks.
"""

import pandas as pd
import numpy as np
from loguru import logger


class TechnicalScorer:

    def __init__(self):
        self.weights = {
            "trend_structure": 0.25,
            "pullback_quality": 0.20,
            "volume_pattern": 0.15,
            "macd_position": 0.15,
            "rsi_setup": 0.10,
            "price_action": 0.10,
            "bollinger_setup": 0.05,
        }

    def score(self, df):
        if len(df) < 200:
            return {"total_score": 0, "details": {}}
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        recent = df.tail(20)
        r5 = df.tail(5)
        scores = {}

        # TREND STRUCTURE: Is the stock in an uptrend?
        # We want: price > 50 SMA > 200 SMA (confirmed)
        a50 = latest.get("above_ma_50", 0)
        a200 = latest.get("above_ma_200", 0)
        ratio = latest.get("ma_50_200_ratio", 1.0)

        if a50 and a200 and ratio > 1.0:
            scores["trend_structure"] = 85
            if ratio > 1.05:
                scores["trend_structure"] = 90
        elif a200 and ratio > 0.98:
            scores["trend_structure"] = 70
        elif a200:
            scores["trend_structure"] = 55
        elif a50:
            scores["trend_structure"] = 40
        else:
            scores["trend_structure"] = 15

        # PULLBACK QUALITY: Has it pulled back to support?
        # Best: price near 20 SMA or 50 SMA within uptrend
        a10 = latest.get("above_ma_10", 0)
        a20 = latest.get("above_ma_20", 0)
        price = latest["close"]

        # Distance below recent high
        high_5d = r5["high"].max()
        pullback_pct = (high_5d - price) / high_5d

        if a50 and a200:
            if not a20 and pullback_pct > 0.02:
                scores["pullback_quality"] = 85
            elif not a10 and a20 and pullback_pct > 0.01:
                scores["pullback_quality"] = 75
            elif pullback_pct > 0.03:
                scores["pullback_quality"] = 80
            elif pullback_pct > 0.01:
                scores["pullback_quality"] = 60
            else:
                scores["pullback_quality"] = 35
        elif a200:
            if pullback_pct > 0.03:
                scores["pullback_quality"] = 65
            else:
                scores["pullback_quality"] = 40
        else:
            scores["pullback_quality"] = 20

        # VOLUME PATTERN: 3+ days accumulation pattern
        # Best: declining volume on pullback, then spike
        if len(df) >= 10:
            vol_5d = df["volume"].tail(5).values
            vol_10d_avg = df["volume"].tail(10).mean()
            up_vol_days = 0
            for i in range(-5, 0):
                row = df.iloc[i]
                if row["close"] > row["open"]:
                    if row["volume"] > vol_10d_avg:
                        up_vol_days += 1

            vol_today = latest.get("volume_ratio", 1.0)
            pullback_dry = all(
                v < vol_10d_avg * 0.8
                for v in vol_5d[:-1]
            )

            if pullback_dry and vol_today > 1.3:
                scores["volume_pattern"] = 90
            elif up_vol_days >= 3:
                scores["volume_pattern"] = 75
            elif up_vol_days >= 2:
                scores["volume_pattern"] = 60
            elif vol_today > 1.5:
                scores["volume_pattern"] = 55
            else:
                scores["volume_pattern"] = 40
        else:
            scores["volume_pattern"] = 50

        # MACD POSITION: Best is histogram negative
        # but turning up (bearish momentum fading)
        mh = latest.get("macd_histogram", 0)
        mh_prev = prev.get("macd_histogram", 0)

        if mh < 0 and mh > mh_prev:
            scores["macd_position"] = 85
        elif mh > 0 and mh > mh_prev:
            scores["macd_position"] = 70
        elif mh < 0 and mh <= mh_prev:
            scores["macd_position"] = 30
        elif mh > 0 and mh <= mh_prev:
            scores["macd_position"] = 50
        else:
            scores["macd_position"] = 45

        if mh > 0 and mh_prev <= 0:
            scores["macd_position"] = 90

        # RSI SETUP: Want RSI 30-45 in uptrend (oversold pullback)
        rsi = latest.get("rsi_14", 50)
        rsi_prev = prev.get("rsi_14", 50)
        rsi_rising = rsi > rsi_prev

        in_uptrend = scores["trend_structure"] >= 55

        if in_uptrend and 30 <= rsi <= 45:
            scores["rsi_setup"] = 85
            if rsi_rising:
                scores["rsi_setup"] = 90
        elif in_uptrend and 45 < rsi <= 55:
            scores["rsi_setup"] = 65
        elif rsi < 30:
            scores["rsi_setup"] = 50
        elif rsi > 70:
            scores["rsi_setup"] = 20
        elif 55 < rsi <= 65:
            scores["rsi_setup"] = 45
        else:
            scores["rsi_setup"] = 40

        # PRICE ACTION: Reversal candle at support
        body = abs(latest["close"] - latest["open"])
        rng = latest["high"] - latest["low"]
        br = body / rng if rng > 0 else 0
        bull = latest["close"] > latest["open"]
        lw = (
            min(latest["close"], latest["open"])
            - latest["low"]
        )

        if bull and lw > body * 2:
            scores["price_action"] = 85
        elif bull and br > 0.6:
            scores["price_action"] = 70
        elif bull and lw > body:
            scores["price_action"] = 60
        elif not bull and br > 0.6:
            scores["price_action"] = 25
        else:
            scores["price_action"] = 45

        # BOLLINGER: Squeeze near lower band in uptrend
        bb_pos = latest.get("bb_position", 0.5)
        bb_w = latest.get("bb_width", 0)
        if "bb_width" in recent.columns:
            bb_q = recent["bb_width"].quantile(0.3)
        else:
            bb_q = bb_w

        if in_uptrend and bb_pos < 0.3:
            scores["bollinger_setup"] = 80
            if bb_w < bb_q:
                scores["bollinger_setup"] = 90
        elif bb_pos < 0.2:
            scores["bollinger_setup"] = 60
        elif bb_pos > 0.9:
            scores["bollinger_setup"] = 20
        else:
            scores["bollinger_setup"] = 50

        total = sum(
            scores[s] * w
            for s, w in self.weights.items()
        )
        return {
            "total_score": round(total, 1),
            "details": {
                k: round(v, 1)
                for k, v in scores.items()
            },
        }

    def score_for_puts(self, df):
        result = self.score(df)
        inv = 100 - result["total_score"]
        return {
            "total_score": round(inv, 1),
            "details": {
                k: round(100 - v, 1)
                for k, v in result["details"].items()
            },
        }
