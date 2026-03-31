"""
Position Lifecycle Tracker.
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from loguru import logger


class Position:

    def __init__(self, symbol, instrument, direction, entry_price, quantity, stop_loss, target_1, target_2, target_3, entry_date, signal_score, sector="", max_hold_days=7, entry_cost=0):
        self.symbol = symbol
        self.instrument = instrument
        self.direction = direction
        self.entry_price = entry_price
        self.original_quantity = quantity
        self.current_quantity = quantity
        self.stop_loss = stop_loss
        self.original_stop = stop_loss
        self.target_1 = target_1
        self.target_2 = target_2
        self.target_3 = target_3
        self.entry_date = entry_date
        self.entry_cost = entry_cost or (entry_price * quantity)
        self.signal_score = signal_score
        self.sector = sector
        self.max_hold_days = max_hold_days
        self.status = "OPEN"
        self.scale_stage = 0
        self.highest_price = entry_price
        self.lowest_price = entry_price
        self.trailing_stop = None
        self.realized_pnl = 0.0
        self.exit_details = []

    def days_held(self):
        entry = datetime.strptime(self.entry_date, "%Y-%m-%d").date()
        today = date.today()
        bdays = 0
        current = entry
        while current < today:
            current += timedelta(days=1)
            if current.weekday() < 5:
                bdays += 1
        return bdays

    def is_expired(self):
        return self.days_held() >= self.max_hold_days

    def update_high_low(self, h, l):
        self.highest_price = max(self.highest_price, h)
        self.lowest_price = min(self.lowest_price, l)

    def unrealized_pnl(self, price):
        if self.direction == "LONG":
            return (price - self.entry_price) * self.current_quantity
        return (self.entry_price - price) * self.current_quantity

    def unrealized_pnl_pct(self, price):
        if self.entry_cost == 0:
            return 0
        cv = price * self.current_quantity
        if self.direction == "LONG":
            return (cv - self.entry_cost) / self.entry_cost
        return (self.entry_cost - cv) / self.entry_cost

    def record_partial_exit(self, qty, exit_price, reason):
        if self.direction == "LONG":
            pnl = (exit_price - self.entry_price) * qty
        else:
            pnl = (self.entry_price - exit_price) * qty
        self.realized_pnl += pnl
        self.current_quantity -= qty
        self.exit_details.append({"quantity": qty, "price": exit_price, "pnl": round(pnl, 2), "reason": reason, "date": date.today().isoformat()})
        if self.current_quantity <= 0:
            self.status = "CLOSED"
            self.current_quantity = 0
        else:
            self.status = "SCALING"
        logger.info(f"Exit {qty} {self.symbol} @ ${exit_price:.2f} ({reason}) P&L: ${pnl:+.2f}")

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d):
        pos = cls.__new__(cls)
        pos.__dict__.update(d)
        return pos


class PositionTracker:

    STATE_PATH = "config/positions.json"

    def __init__(self):
        self.positions = {}
        self.closed_positions = []
        self._load_state()

    def _load_state(self):
        if Path(self.STATE_PATH).exists():
            with open(self.STATE_PATH) as f:
                data = json.load(f)
            for key, pd in data.get("open", {}).items():
                self.positions[key] = Position.from_dict(pd)
            self.closed_positions = data.get("closed", [])
            logger.info(f"Loaded {len(self.positions)} open positions")

    def save_state(self):
        data = {"open": {k: v.to_dict() for k, v in self.positions.items()}, "closed": self.closed_positions[-500:], "last_updated": datetime.utcnow().isoformat()}
        Path(self.STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(self.STATE_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def open_position(self, position):
        key = f"{position.symbol}_{position.instrument}_{date.today().isoformat()}"
        self.positions[key] = position
        self.save_state()
        logger.info(f"Opened: {key} {position.direction} {position.current_quantity} {position.symbol} @ ${position.entry_price:.2f}")
        return key

    def close_position(self, key, exit_price, reason):
        if key not in self.positions:
            return
        pos = self.positions[key]
        pos.record_partial_exit(pos.current_quantity, exit_price, reason)
        self.closed_positions.append(pos.to_dict())
        del self.positions[key]
        self.save_state()

    def partial_close(self, key, qty, exit_price, reason):
        if key not in self.positions:
            return
        pos = self.positions[key]
        pos.record_partial_exit(qty, exit_price, reason)
        if pos.status == "CLOSED":
            self.closed_positions.append(pos.to_dict())
            del self.positions[key]
        self.save_state()

    def get_open(self):
        return {k: v for k, v in self.positions.items() if v.status != "CLOSED"}

    def by_instrument(self, inst):
        return [p for p in self.positions.values() if p.instrument == inst and p.status != "CLOSED"]

    def by_sector(self, sector):
        return [p for p in self.positions.values() if p.sector == sector and p.status != "CLOSED"]

    def total_deployed(self):
        return sum(p.entry_price * p.current_quantity for p in self.positions.values() if p.status != "CLOSED")

    def get_summary(self, prices=None):
        prices = prices or {}
        op = self.get_open()
        upnl = sum(p.unrealized_pnl(prices.get(p.symbol, p.entry_price)) for p in op.values())
        rpnl = sum(p.get("realized_pnl", 0) for p in self.closed_positions)
        return {"open_count": len(op), "options_count": len(self.by_instrument("CALL")) + len(self.by_instrument("PUT")), "stock_count": len(self.by_instrument("STOCK")), "etf_count": len(self.by_instrument("ETF")), "total_deployed": round(self.total_deployed(), 2), "total_unrealized_pnl": round(upnl, 2), "total_realized_pnl": round(rpnl, 2), "positions": {k: {"symbol": v.symbol, "instrument": v.instrument, "direction": v.direction, "quantity": v.current_quantity, "entry": v.entry_price, "stop": v.stop_loss, "days_held": v.days_held(), "stage": v.scale_stage} for k, v in op.items()}}
