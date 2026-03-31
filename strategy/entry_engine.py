"""
Entry Engine - Order Construction.
"""

from datetime import datetime, date, time
from loguru import logger
from strategy.portfolio_manager import PortfolioManager
from strategy.position_tracker import Position


class EntryEngine:

    EARLIEST = time(10, 0)
    LATEST = time(15, 30)

    def __init__(self, pm):
        self.pm = pm
        self.tracker = pm.tracker

    def is_window_open(self):
        now = datetime.now().time()
        return self.EARLIEST <= now <= self.LATEST

    def process_watchlist(self, watchlist):
        orders = []
        if self.pm.halted:
            logger.warning(f"Trading halted: {self.pm.halt_reason}")
            return orders
        for pick in watchlist.get("stocks", []):
            o = self._prep_stock(pick)
            if o:
                orders.append(o)
        for pick in watchlist.get("options", []):
            o = self._prep_option(pick)
            if o:
                orders.append(o)
        for pick in watchlist.get("leveraged_etfs", []):
            o = self._prep_etf(pick)
            if o:
                orders.append(o)
        logger.info(f"Entry engine produced {len(orders)} orders")
        return orders

    def _prep_stock(self, pick):
        sym = pick["symbol"]
        sec = pick.get("sector", "")
        price = pick["entry_price"]
        atr = pick.get("atr", price * 0.02)
        score = pick.get("score", 0)
        for p in self.tracker.by_instrument("STOCK"):
            if p.symbol == sym:
                return None
        mod = 1.0 if score >= 70 else 0.5
        sizing = self.pm.get_size("STOCK", price, atr, mod)
        shares = sizing.get("shares", 0)
        cost = sizing.get("cost", 0)
        if shares <= 0:
            return None
        ok, reason = self.pm.can_enter("STOCK", sec, cost)
        if not ok:
            logger.info(f"Blocked {sym}: {reason}")
            return None
        lp = round(price * 1.003, 2)
        return {"type": "STOCK_BUY", "symbol": sym, "shares": shares, "limit_price": lp, "stop_loss": pick["stop_loss"], "target_1": pick.get("target_1"), "target_2": pick.get("target_2"), "target_3": pick.get("target_3"), "atr": atr, "sector": sec, "signal_score": score, "cost": cost, "max_hold_days": 7}

    def _prep_option(self, pick):
        sym = pick["symbol"]
        direction = pick["direction"]
        mc = pick.get("max_cost", 400)
        score = pick.get("score", 0)
        cur = self.tracker.by_instrument("CALL") + self.tracker.by_instrument("PUT")
        if len(cur) >= 3:
            return None
        for p in cur:
            if p.symbol == sym:
                return None
        ok, reason = self.pm.can_enter(direction, "", mc)
        if not ok:
            logger.info(f"Options blocked {sym}: {reason}")
            return None
        return {"type": f"OPTIONS_{direction}", "symbol": sym, "direction": direction, "max_cost": min(mc, 400), "signal_score": score, "stop_loss_pct": 0.35, "target_1_pct": 0.50, "max_hold_days": 5, "entry_price": pick.get("entry_price")}

    def _prep_etf(self, pick):
        sym = pick["symbol"]
        price = pick["entry_price"]
        score = pick.get("score", 0)
        cur = self.tracker.by_instrument("ETF")
        if len(cur) >= 2:
            return None
        for p in cur:
            if p.symbol == sym:
                return None
        mod = 1.0 if score >= 70 else 0.5
        sizing = self.pm.get_size("ETF", price, 0, mod)
        shares = sizing.get("shares", 0)
        cost = sizing.get("cost", 0)
        if shares <= 0:
            return None
        ok, reason = self.pm.can_enter("ETF", "", cost)
        if not ok:
            logger.info(f"ETF blocked {sym}: {reason}")
            return None
        lp = round(price * 1.003, 2)
        sp = round(price * 0.95, 2)
        return {"type": "ETF_BUY", "symbol": sym, "shares": shares, "limit_price": lp, "stop_loss": sp, "direction": pick.get("direction", "BULLISH"), "signal_score": score, "cost": cost, "max_hold_days": 5}
