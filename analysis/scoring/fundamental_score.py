"""
Fundamental Quality Scoring Module (15% weight).
"""

import yfinance as yf
import pandas as pd
from loguru import logger
from datetime import datetime, date


class FundamentalScorer:

    def __init__(self):
        self._cache = {}
        self._cache_time = {}

    def score(self, symbol):
        now = datetime.utcnow()
        if symbol in self._cache:
            age = (now - self._cache_time[symbol]).total_seconds()
            if age < 7 * 24 * 3600:
                return self._cache[symbol]
        scores = {}
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            rg = info.get("revenueGrowth", 0) or 0
            if rg > 0.20:
                scores["revenue_growth"] = 90
            elif rg > 0.10:
                scores["revenue_growth"] = 75
            elif rg > 0:
                scores["revenue_growth"] = 60
            elif rg > -0.10:
                scores["revenue_growth"] = 35
            else:
                scores["revenue_growth"] = 15
            try:
                eh = ticker.earnings_dates
                if eh is not None and len(eh) > 0:
                    s = eh.get("Surprise(%)", pd.Series())
                    if len(s.dropna()) >= 2:
                        avg = s.dropna().head(4).mean()
                        if avg > 5:
                            scores["earnings_surprise"] = 85
                        elif avg > 0:
                            scores["earnings_surprise"] = 65
                        elif avg > -5:
                            scores["earnings_surprise"] = 40
                        else:
                            scores["earnings_surprise"] = 20
                    else:
                        scores["earnings_surprise"] = 50
                else:
                    scores["earnings_surprise"] = 50
            except Exception:
                scores["earnings_surprise"] = 50
            pm = info.get("profitMargins", 0) or 0
            if pm > 0.20:
                scores["profitability"] = 85
            elif pm > 0.10:
                scores["profitability"] = 70
            elif pm > 0:
                scores["profitability"] = 55
            else:
                scores["profitability"] = 25
            rec = info.get("recommendationMean", 3)
            if rec and rec <= 1.8:
                scores["analyst_sentiment"] = 85
            elif rec and rec <= 2.3:
                scores["analyst_sentiment"] = 70
            elif rec and rec <= 3.0:
                scores["analyst_sentiment"] = 50
            elif rec and rec <= 3.5:
                scores["analyst_sentiment"] = 35
            else:
                scores["analyst_sentiment"] = 20
            ne = info.get("earningsDate")
            if ne:
                if isinstance(ne, list) and len(ne) > 0:
                    ne = ne[0]
                try:
                    if hasattr(ne, "date"):
                        ed = ne.date()
                    else:
                        ed = pd.Timestamp(ne).date()
                    dte = (ed - date.today()).days
                    if 0 <= dte <= 5:
                        scores["earnings_proximity"] = 0
                    elif 5 < dte <= 14:
                        scores["earnings_proximity"] = 50
                    else:
                        scores["earnings_proximity"] = 75
                except Exception:
                    scores["earnings_proximity"] = 60
            else:
                scores["earnings_proximity"] = 65
        except Exception as e:
            logger.warning(f"Fundamental error {symbol}: {e}")
            return {"total_score": 50, "details": {"error": str(e)}}
        w = {"revenue_growth": 0.25, "earnings_surprise": 0.20, "profitability": 0.20, "analyst_sentiment": 0.15, "earnings_proximity": 0.20}
        total = sum(scores.get(k, 50) * v for k, v in w.items())
        result = {"total_score": round(total, 1), "details": {k: round(v, 1) for k, v in scores.items()}, "earnings_blocked": scores.get("earnings_proximity", 75) == 0}
        self._cache[symbol] = result
        self._cache_time[symbol] = now
        return result
