"""
Pre-Trade Validator - Final checkpoint before broker.
"""

import json
from datetime import datetime, date, time
from pathlib import Path
from loguru import logger


class PreTradeValidator:

    MAX_ORDERS_PER_DAY = 20
    MAX_SINGLE_PCT = 0.12
    MAX_PRICE_DEV = 0.05
    MIN_INTERVAL_SEC = 30

    def __init__(self, equity):
        self.equity = equity
        self.history = self._load()
        self.blacklist = self._load_bl()

    def _load(self):
        p = Path("config/order_history.json")
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            today = date.today().isoformat()
            return [o for o in data if o.get("date") == today]
        return []

    def _save(self):
        Path("config").mkdir(exist_ok=True)
        with open("config/order_history.json", "w") as f:
            json.dump(self.history[-100:], f, indent=2, default=str)

    def _load_bl(self):
        p = Path("config/blacklist.json")
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            return set(data.get(date.today().isoformat(), []))
        return set()

    def validate(self, order, prices=None, state=None):
        prices = prices or {}
        state = state or {}
        sym = order.get("symbol", "")
        otype = order.get("type", "")
        lp = order.get("limit_price", 0)
        qty = order.get("shares", order.get("quantity", 0))
        cost = order.get("cost", order.get("max_cost", 0))

        # Market hours
        now = datetime.now().time()
        if time(20, 0) <= now or now <= time(4, 0):
            return False, "Outside reasonable hours"

        # Blacklist
        if sym in self.blacklist:
            return False, f"{sym} blacklisted today"

        # Duplicate
        now_dt = datetime.utcnow()
        for recent in reversed(self.history):
            if recent["symbol"] != sym:
                continue
            try:
                ot = datetime.fromisoformat(recent["time"])
                if (now_dt - ot).total_seconds() < self.MIN_INTERVAL_SEC:
                    return False, f"Duplicate order for {sym}"
            except (ValueError, KeyError):
                continue

        # Throttle
        today = date.today().isoformat()
        tc = sum(1 for o in self.history if o.get("date") == today)
        if tc >= self.MAX_ORDERS_PER_DAY:
            return False, f"Daily limit ({self.MAX_ORDERS_PER_DAY})"

        # Fat finger
        if qty <= 0:
            return False, "Zero quantity"
        if cost > self.equity * self.MAX_SINGLE_PCT:
            return False, f"Cost exceeds {self.MAX_SINGLE_PCT:.0%} of equity"
        if qty > 10000:
            return False, f"Suspicious quantity: {qty}"

        # Price sanity
        if lp <= 0:
            return False, "Invalid limit price"
        cur = prices.get(sym)
        if cur and cur > 0:
            dev = abs(lp - cur) / cur
            if dev > self.MAX_PRICE_DEV:
                return False, f"Price ${lp:.2f} is {dev:.1%} from ${cur:.2f}"

        # Buying power
        if "SELL" not in otype and cost > self.equity * 0.75:
            return False, f"Cost too large relative to equity"

        # Record
        self.history.append({"date": date.today().isoformat(), "time": datetime.utcnow().isoformat(), "symbol": sym, "type": otype, "quantity": qty, "limit_price": lp})
        self._save()
        return True, "APPROVED"
