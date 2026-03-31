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
