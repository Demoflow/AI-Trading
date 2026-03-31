"""
Schwab order execution module.
Handles paper and live trading.
"""

import os
import json
import httpx
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


class SchwabExecutor:

    def __init__(self, client=None, account_hash=None, paper_mode=True):
        self.client = client
        self.account_hash = account_hash
        self.paper_mode = paper_mode
        self.order_log = []
        if paper_mode:
            logger.info("PAPER MODE - no real orders")
            self._pp = self._load_paper()
        else:
            if not client or not account_hash:
                raise ValueError("Client and hash required for live")
            logger.info("LIVE MODE")

    def _load_paper(self):
        p = "config/paper_portfolio.json"
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return {"cash": 8000.0, "positions": {}}

    def _save_paper(self):
        with open("config/paper_portfolio.json", "w") as f:
            json.dump(self._pp, f, indent=2)

    def get_current_quote(self, symbol):
        if self.client:
            try:
                resp = self.client.get_quote(symbol)
                if resp.status_code == httpx.codes.OK:
                    data = resp.json()
                    q = data.get(symbol, {}).get("quote", {})
                    return {"bid": q.get("bidPrice", 0), "ask": q.get("askPrice", 0), "last": q.get("lastPrice", 0)}
            except Exception:
                pass
        return {"bid": 0, "ask": 0, "last": 0}

    def submit_order(self, symbol, side, quantity, limit_price=None):
        if quantity <= 0:
            return {"status": "SKIPPED", "reason": "zero_quantity"}
        if limit_price is None:
            q = self.get_current_quote(symbol)
            if side == "BUY":
                limit_price = round(q.get("ask", 0) * 1.005, 2)
            else:
                limit_price = round(q.get("bid", 0) * 0.995, 2)
        if limit_price <= 0:
            return {"status": "ERROR", "reason": "invalid_price"}
        if self.paper_mode:
            return self._sim_fill(symbol, side, quantity, limit_price)
        try:
            from schwab.orders.equities import equity_buy_limit, equity_sell_limit
            if side == "BUY":
                order = equity_buy_limit(symbol, quantity, limit_price)
            else:
                order = equity_sell_limit(symbol, quantity, limit_price)
            resp = self.client.place_order(self.account_hash, order)
            if resp.status_code in (httpx.codes.CREATED, httpx.codes.OK):
                from schwab.utils import Utils
                oid = Utils.extract_order_id(resp)
                result = {"status": "SUBMITTED", "order_id": oid, "symbol": symbol, "side": side, "quantity": quantity, "limit_price": limit_price, "timestamp": datetime.utcnow().isoformat()}
                logger.info(f"Order: {side} {quantity} {symbol} @ ${limit_price:.2f} (ID: {oid})")
                self.order_log.append(result)
                return result
            else:
                return {"status": "REJECTED", "reason": str(resp.status_code)}
        except Exception as e:
            logger.error(f"Order failed {symbol}: {e}")
            return {"status": "ERROR", "reason": str(e)}

    def _sim_fill(self, symbol, side, qty, price):
        cost = qty * price
        if side == "BUY":
            if cost > self._pp["cash"]:
                return {"status": "REJECTED", "reason": "insufficient_cash"}
            self._pp["cash"] -= cost
            cur = self._pp["positions"].get(symbol, {})
            cq = cur.get("quantity", 0)
            ca = cur.get("avg_price", 0)
            nq = cq + qty
            na = ((ca * cq) + (price * qty)) / nq if nq > 0 else price
            self._pp["positions"][symbol] = {"quantity": nq, "avg_price": round(na, 4)}
        elif side == "SELL":
            cur = self._pp["positions"].get(symbol, {})
            cq = cur.get("quantity", 0)
            if qty > cq:
                return {"status": "REJECTED", "reason": "insufficient_shares"}
            self._pp["cash"] += cost
            nq = cq - qty
            if nq == 0:
                del self._pp["positions"][symbol]
            else:
                self._pp["positions"][symbol]["quantity"] = nq
        self._save_paper()
        fill = {"status": "FILLED", "symbol": symbol, "side": side, "quantity": qty, "fill_price": price, "timestamp": datetime.utcnow().isoformat(), "paper": True}
        logger.info(f"PAPER {side}: {qty} {symbol} @ ${price:.2f}")
        self.order_log.append(fill)
        return fill

    def get_paper_summary(self):
        if not self.paper_mode:
            return {}
        tv = self._pp["cash"]
        details = []
        for sym, pos in self._pp["positions"].items():
            q = self.get_current_quote(sym)
            cp = q.get("last", pos["avg_price"])
            mv = pos["quantity"] * cp
            tv += mv
            pnl = (cp - pos["avg_price"]) * pos["quantity"]
            details.append({"symbol": sym, "quantity": pos["quantity"], "avg_price": pos["avg_price"], "current_price": cp, "market_value": round(mv, 2), "pnl": round(pnl, 2)})
        return {"cash": round(self._pp["cash"], 2), "total_value": round(tv, 2), "positions": details}
