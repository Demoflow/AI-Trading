"""
Market Context Scoring Module (15% weight).
"""

from loguru import logger


class MarketContextScorer:

    def score(self, symbol, stock_sector, spy_df, sector_df=None, vix_price=None, stock_df=None):
        scores = {}
        if spy_df is not None and len(spy_df) >= 50:
            sp = spy_df.iloc[-1]["close"]
            s20 = spy_df["close"].tail(20).mean()
            s50 = spy_df["close"].tail(50).mean()
            r5 = spy_df["close"].pct_change(5).iloc[-1]
            if sp > s20 > s50:
                scores["spy_trend"] = 80
            elif sp > s20:
                scores["spy_trend"] = 65
            elif sp > s50:
                scores["spy_trend"] = 50
            elif sp < s20 < s50:
                scores["spy_trend"] = 20
            else:
                scores["spy_trend"] = 35
            if r5 > 0.02:
                scores["spy_trend"] = min(95, scores["spy_trend"] + 10)
            elif r5 < -0.02:
                scores["spy_trend"] = max(10, scores["spy_trend"] - 10)
        else:
            scores["spy_trend"] = 50
        if vix_price is not None:
            if vix_price < 15:
                scores["vix_regime"] = 75
            elif vix_price < 20:
                scores["vix_regime"] = 65
            elif vix_price < 25:
                scores["vix_regime"] = 45
            elif vix_price < 30:
                scores["vix_regime"] = 30
            else:
                scores["vix_regime"] = 15
        else:
            scores["vix_regime"] = 55
        if sector_df is not None and len(sector_df) >= 5:
            sr = sector_df["close"].pct_change(5).iloc[-1]
            spr = spy_df["close"].pct_change(5).iloc[-1] if spy_df is not None and len(spy_df) >= 5 else 0
            vs = sr - spr
            if vs > 0.02:
                scores["sector_strength"] = 85
            elif vs > 0.005:
                scores["sector_strength"] = 70
            elif vs > -0.005:
                scores["sector_strength"] = 50
            elif vs > -0.02:
                scores["sector_strength"] = 35
            else:
                scores["sector_strength"] = 20
        else:
            scores["sector_strength"] = 50
        if stock_df is not None and sector_df is not None and len(stock_df) >= 5 and len(sector_df) >= 5:
            stk = stock_df["close"].pct_change(5).iloc[-1]
            sec = sector_df["close"].pct_change(5).iloc[-1]
            d = stk - sec
            if d > 0.03:
                scores["stock_vs_sector"] = 85
            elif d > 0.01:
                scores["stock_vs_sector"] = 70
            elif d > -0.01:
                scores["stock_vs_sector"] = 50
            else:
                scores["stock_vs_sector"] = 30
        else:
            scores["stock_vs_sector"] = 50
        w = {"spy_trend": 0.35, "vix_regime": 0.25, "sector_strength": 0.20, "stock_vs_sector": 0.20}
        total = sum(scores.get(k, 50) * v for k, v in w.items())
        return {"total_score": round(total, 1), "details": {k: round(v, 1) for k, v in scores.items()}}
