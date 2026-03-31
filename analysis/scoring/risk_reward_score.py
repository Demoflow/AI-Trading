"""
Risk/Reward Scoring (15% weight).
Uses meaningful support/resistance levels, not just
20-day highs/lows.
"""

from loguru import logger


class RiskRewardScorer:

    def score(self, df):
        if len(df) < 60:
            return {"total_score": 50, "details": {}}
        latest = df.iloc[-1]
        scores = {}
        price = latest["close"]
        atr = latest.get("atr_14", price * 0.02)

        # REAL RESISTANCE: 52-week high, not 20-day
        high_252 = df.tail(252)["high"].max()
        high_60 = df.tail(60)["high"].max()

        # REAL SUPPORT: 50 SMA, 200 SMA, 60-day low
        sma_50 = df["close"].tail(50).mean()
        sma_200 = df["close"].tail(200).mean()
        low_60 = df.tail(60)["low"].min()

        # Best support = highest of the three below price
        supports = []
        if sma_50 < price:
            supports.append(("sma_50", sma_50))
        if sma_200 < price:
            supports.append(("sma_200", sma_200))
        supports.append(("low_60d", low_60))

        if supports:
            best_support = max(supports, key=lambda x: x[1])
            support_name = best_support[0]
            support_level = best_support[1]
        else:
            support_name = "atr_2x"
            support_level = price - (2 * atr)

        # Distance to support (stop placement)
        stop_distance = price - support_level
        stop_pct = stop_distance / price

        if stop_pct < 0.02:
            scores["stop_quality"] = 85
        elif stop_pct < 0.04:
            scores["stop_quality"] = 70
        elif stop_pct < 0.06:
            scores["stop_quality"] = 55
        elif stop_pct < 0.08:
            scores["stop_quality"] = 40
        else:
            scores["stop_quality"] = 20

        # Distance to resistance (upside)
        dist_to_52w = (high_252 - price) / price
        dist_to_60d = (high_60 - price) / price

        if dist_to_52w > 0.15:
            scores["upside_room"] = 85
        elif dist_to_52w > 0.10:
            scores["upside_room"] = 75
        elif dist_to_52w > 0.05:
            scores["upside_room"] = 60
        elif dist_to_60d > 0.03:
            scores["upside_room"] = 50
        else:
            scores["upside_room"] = 30

        # Actual reward-to-risk ratio
        upside = high_60 - price
        downside = stop_distance
        rr = upside / downside if downside > 0 else 1
        if rr > 3.0:
            scores["rr_ratio"] = 90
        elif rr > 2.5:
            scores["rr_ratio"] = 80
        elif rr > 2.0:
            scores["rr_ratio"] = 70
        elif rr > 1.5:
            scores["rr_ratio"] = 55
        elif rr > 1.0:
            scores["rr_ratio"] = 40
        else:
            scores["rr_ratio"] = 20

        # ATR volatility sweet spot
        atr_pct = latest.get("atr_pct_14", 0.02)
        if 0.015 <= atr_pct <= 0.04:
            scores["volatility_fit"] = 75
        elif 0.01 <= atr_pct < 0.015:
            scores["volatility_fit"] = 55
        elif atr_pct > 0.06:
            scores["volatility_fit"] = 30
        else:
            scores["volatility_fit"] = 50

        w = {
            "stop_quality": 0.30,
            "upside_room": 0.20,
            "rr_ratio": 0.30,
            "volatility_fit": 0.20,
        }
        total = sum(scores[k] * w[k] for k in w)

        # Calculate recommended stop level
        rec_stop = round(support_level - (0.5 * atr), 2)

        return {
            "total_score": round(total, 1),
            "details": {
                k: round(v, 1)
                for k, v in scores.items()
            },
            "levels": {
                "support": round(support_level, 2),
                "support_type": support_name,
                "resistance_60d": round(high_60, 2),
                "resistance_52w": round(high_252, 2),
                "recommended_stop": rec_stop,
                "atr_14": round(atr, 2),
                "rr_ratio": round(rr, 2),
                "stop_distance_pct": round(stop_pct, 4),
            },
        }
