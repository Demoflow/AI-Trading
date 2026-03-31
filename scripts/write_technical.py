import os

files = {}

files["analysis/scoring/fundamental_score.py"] = '''\
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
'''

files["analysis/scoring/market_context_score.py"] = '''\
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
'''

files["analysis/scoring/risk_reward_score.py"] = '''\
"""
Risk/Reward Scoring Module (15% weight).
"""

from loguru import logger


class RiskRewardScorer:

    def score(self, df):
        if len(df) < 60:
            return {"total_score": 50, "details": {}}
        latest = df.iloc[-1]
        r60 = df.tail(60)
        scores = {}
        price = latest["close"]
        atr = latest.get("atr_14", price * 0.02)
        res = r60.tail(20)["high"].max()
        sup = r60.tail(20)["low"].min()
        dr = (res - price) / price
        if dr > 0.05:
            scores["room_to_run"] = 80
        elif dr > 0.02:
            scores["room_to_run"] = 65
        elif dr > 0:
            scores["room_to_run"] = 45
        else:
            scores["room_to_run"] = 55
        ds = (price - sup) / price
        if ds < 0.02:
            scores["support_proximity"] = 80
        elif ds < 0.05:
            scores["support_proximity"] = 65
        elif ds < 0.10:
            scores["support_proximity"] = 50
        else:
            scores["support_proximity"] = 35
        up = res - price
        dn = price - sup
        rr = up / dn if dn > 0 else 3.0
        if rr > 3.0:
            scores["rr_ratio"] = 90
        elif rr > 2.0:
            scores["rr_ratio"] = 75
        elif rr > 1.5:
            scores["rr_ratio"] = 60
        elif rr > 1.0:
            scores["rr_ratio"] = 45
        else:
            scores["rr_ratio"] = 25
        emp = (2 * atr) / price
        if emp > 0.06:
            scores["atr_potential"] = 75
        elif emp > 0.04:
            scores["atr_potential"] = 65
        elif emp > 0.02:
            scores["atr_potential"] = 50
        else:
            scores["atr_potential"] = 35
        w = {"room_to_run": 0.25, "support_proximity": 0.25, "rr_ratio": 0.30, "atr_potential": 0.20}
        total = sum(scores[k] * w[k] for k in w)
        return {"total_score": round(total, 1), "details": {k: round(v, 1) for k, v in scores.items()}, "levels": {"resistance_20d": round(res, 2), "support_20d": round(sup, 2), "atr_14": round(atr, 2), "rr_ratio": round(rr, 2)}}
'''

files["analysis/signals/signal_generator.py"] = '''\
"""
Master Signal Generator.
"""

import os
import sys
import csv
import pandas as pd
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data.storage.database import get_session
from data.storage.models import Stock, DailyPrice
from features.technical import compute_all_features
from analysis.scoring.composite import CompositeScorer


class SignalGenerator:

    def __init__(self, schwab_client=None):
        self.scorer = CompositeScorer()
        self.schwab_client = schwab_client

    def load_price_data(self, symbol, days=250):
        with get_session() as session:
            rows = session.query(DailyPrice).filter(DailyPrice.symbol == symbol).order_by(DailyPrice.date.desc()).limit(days).all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([{"date": r.date, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "adj_close": r.adj_close, "volume": r.volume} for r in reversed(rows)])
        df = compute_all_features(df)
        return df

    def get_vix(self):
        return 20

    def get_options_chain(self, symbol):
        return None

    def run_full_scan(self):
        logger.info("=" * 60)
        logger.info("STARTING FULL UNIVERSE SCAN")
        logger.info("=" * 60)
        universe = []
        with open("config/universe.csv") as f:
            reader = csv.DictReader(f)
            universe = list(reader)
        tradeable = [s for s in universe if s["market_cap_tier"] in ("mega", "large")]
        logger.info(f"Scanning {len(tradeable)} tradeable stocks")
        spy_df = self.load_price_data("SPY")
        vix = self.get_vix()
        logger.info(f"VIX: {vix}")
        sector_etfs = {"Technology": "XLK", "Healthcare": "XLV", "Financials": "XLF", "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI", "Materials": "XLB"}
        sector_dfs = {}
        for sector, etf in sector_etfs.items():
            sector_dfs[sector] = self.load_price_data(etf)
        all_results = []
        for stock in tradeable:
            symbol = stock["symbol"]
            sector = stock.get("sector", "Technology")
            try:
                stock_df = self.load_price_data(symbol)
                if stock_df.empty or len(stock_df) < 200:
                    continue
                sector_df = sector_dfs.get(sector)
                chain_data = self.get_options_chain(symbol)
                bull = self.scorer.score_stock(symbol, stock_df, spy_df, sector_df, vix, chain_data, sector)
                bull["direction"] = "BULLISH"
                bull["sector"] = sector
                all_results.append(bull)
                bear = self.scorer.score_for_puts(symbol, stock_df, spy_df, sector_df, vix, chain_data, sector)
                bear["direction"] = "BEARISH"
                bear["sector"] = sector
                all_results.append(bear)
            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")
                continue
        all_results.sort(key=lambda x: x["composite_score"], reverse=True)
        actionable = [r for r in all_results if r["action"] in ("ENTER", "ENTER_REDUCED")]
        watchlist = [r for r in all_results if r["action"] == "WATCHLIST"]
        logger.info(f"Scan complete: {len(actionable)} actionable, {len(watchlist)} watchlist")
        for r in actionable[:10]:
            logger.info(f"  {r['direction']:7s} {r['symbol']:5s} Score: {r['composite_score']:5.1f} Action: {r['action']} Entry: ${r['trade_params']['entry_price']:.2f} Stop: ${r['trade_params']['stop_loss']:.2f}")
        return all_results
'''

files["analysis/signals/watchlist.py"] = '''\
"""
Watchlist Generator.
"""

import json
from datetime import datetime, date
from loguru import logger


class WatchlistGenerator:

    MAX_OPTIONS = 3
    MAX_STOCKS = 4
    MAX_ETFS = 2
    LEV_ETFS = {"TQQQ", "SQQQ", "UPRO", "SPXU", "SOXL", "SOXS", "LABU", "TNA", "TZA", "NUGT"}

    def generate(self, scan_results, equity, positions=None):
        positions = positions or {}
        act = [r for r in scan_results if r["action"] in ("ENTER", "ENTER_REDUCED") and r["composite_score"] >= 60 and r.get("override") is None]
        opt_c = []
        stk_c = []
        etf_c = []
        for r in act:
            sym = r["symbol"]
            if sym in self.LEV_ETFS:
                etf_c.append(r)
            elif r["direction"] == "BULLISH":
                stk_c.append(r)
                if r["composite_score"] >= 75:
                    opt_c.append({**r, "instrument": "CALL"})
            elif r["direction"] == "BEARISH":
                if r["composite_score"] >= 75:
                    opt_c.append({**r, "instrument": "PUT"})
        wl = {"generated_at": datetime.utcnow().isoformat(), "account_equity": equity, "options": [], "stocks": [], "leveraged_etfs": [], "summary": {}}
        mo = equity * 0.05
        for c in opt_c[:self.MAX_OPTIONS]:
            sz = mo * c.get("size_modifier", 1.0)
            wl["options"].append({"symbol": c["symbol"], "direction": c["instrument"], "score": c["composite_score"], "max_cost": round(sz, 2), "entry_price": c["trade_params"]["entry_price"], "stop_loss": c["trade_params"]["stop_loss"], "target_1": c["trade_params"]["target_1"], "target_2": c["trade_params"]["target_2"], "sub_scores": c["sub_scores"]})
        ms = equity * 0.08
        sc = {}
        for c in stk_c:
            if len(wl["stocks"]) >= self.MAX_STOCKS:
                break
            sec = c.get("sector", "Unknown")
            if sc.get(sec, 0) >= 2:
                continue
            sz = ms * c.get("size_modifier", 1.0)
            atr = c["trade_params"]["atr"]
            rpt = equity * 0.015
            shares = int(rpt / (2 * atr)) if atr > 0 else 0
            cost = shares * c["trade_params"]["entry_price"]
            if cost > sz:
                shares = int(sz / c["trade_params"]["entry_price"])
            if shares > 0:
                wl["stocks"].append({"symbol": c["symbol"], "score": c["composite_score"], "shares": shares, "position_cost": round(shares * c["trade_params"]["entry_price"], 2), "entry_price": c["trade_params"]["entry_price"], "stop_loss": c["trade_params"]["stop_loss"], "target_1": c["trade_params"]["target_1"], "target_2": c["trade_params"]["target_2"], "target_3": c["trade_params"]["target_3"], "atr": atr, "sector": sec, "sub_scores": c["sub_scores"]})
                sc[sec] = sc.get(sec, 0) + 1
        me = equity * 0.10
        if date.today().weekday() != 4:
            for c in etf_c[:self.MAX_ETFS]:
                sz = me * c.get("size_modifier", 1.0)
                shares = int(sz / c["trade_params"]["entry_price"])
                if shares > 0:
                    wl["leveraged_etfs"].append({"symbol": c["symbol"], "score": c["composite_score"], "shares": shares, "position_cost": round(shares * c["trade_params"]["entry_price"], 2), "entry_price": c["trade_params"]["entry_price"], "stop_loss": c["trade_params"]["stop_loss"], "direction": c["direction"], "sub_scores": c["sub_scores"]})
        tp = sum(o["max_cost"] for o in wl["options"]) + sum(s["position_cost"] for s in wl["stocks"]) + sum(e["position_cost"] for e in wl["leveraged_etfs"])
        wl["summary"] = {"total_candidates_scanned": len(scan_results) // 2, "options_picks": len(wl["options"]), "stock_picks": len(wl["stocks"]), "etf_picks": len(wl["leveraged_etfs"]), "total_planned_deployment": round(tp, 2), "deployment_pct": round(tp / equity * 100, 1) if equity > 0 else 0, "cash_remaining_pct": round((1 - tp / equity) * 100, 1) if equity > 0 else 100}
        return wl

    def save_watchlist(self, wl, path="config/watchlist.json"):
        with open(path, "w") as f:
            json.dump(wl, f, indent=2, default=str)
        logger.info(f"Watchlist saved to {path}")

    def print_watchlist(self, wl):
        logger.info("=" * 60)
        logger.info("TOMORROWS WATCHLIST")
        logger.info("=" * 60)
        if wl["options"]:
            logger.info("OPTIONS:")
            for o in wl["options"]:
                logger.info(f"  {o['direction']:4s} {o['symbol']:5s} Score: {o['score']:5.1f} Max: ${o['max_cost']:.0f}")
        if wl["stocks"]:
            logger.info("STOCKS:")
            for s in wl["stocks"]:
                logger.info(f"  LONG {s['symbol']:5s} Score: {s['score']:5.1f} {s['shares']} shares @ ${s['entry_price']:.2f} Stop: ${s['stop_loss']:.2f} [{s['sector']}]")
        if wl["leveraged_etfs"]:
            logger.info("LEVERAGED ETFs:")
            for e in wl["leveraged_etfs"]:
                logger.info(f"  {e['direction']:7s} {e['symbol']:5s} Score: {e['score']:5.1f} {e['shares']} shares @ ${e['entry_price']:.2f}")
        s = wl["summary"]
        logger.info(f"SUMMARY: {s['options_picks']} options, {s['stock_picks']} stocks, {s['etf_picks']} ETFs")
        logger.info(f"Planned deployment: ${s['total_planned_deployment']:,.0f} ({s['deployment_pct']}% of equity)")
        logger.info(f"Cash remaining: {s['cash_remaining_pct']}%")
'''

for path, content in files.items():
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    print(f"Written: {path}")

print("All files written successfully!")