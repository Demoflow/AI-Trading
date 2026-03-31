"""
Real-Time Position Monitor.
"""

import json
from datetime import datetime, date
from pathlib import Path
from loguru import logger


class RealtimeMonitor:

    POS_WARN = -0.02
    POS_CRIT = -0.04
    PORT_WARN = -0.015
    PORT_CRIT = -0.025
    SPY_WARN = -0.01
    SPY_CRIT = -0.02

    def __init__(self, tracker, equity):
        self.tracker = tracker
        self.equity = equity
        self.sod = self._load_sod()
        self.alerts = []

    def _load_sod(self):
        p = Path("config/sod_equity.json")
        if p.exists():
            with open(p) as f:
                d = json.load(f)
            if d.get("date") == date.today().isoformat():
                return d["equity"]
        return self.equity

    def save_sod(self, eq):
        with open("config/sod_equity.json", "w") as f:
            json.dump({"date": date.today().isoformat(), "equity": eq}, f)
        self.sod = eq

    def run_check(self, prices, spy_price=None, vix=None, broker_eq=None):
        self.alerts = []
        recs = []
        positions = self.tracker.get_open()
        total_pnl = 0
        snaps = []

        for key, pos in positions.items():
            price = prices.get(pos.symbol)
            if price is None:
                continue
            pnl = pos.unrealized_pnl(price)
            pnl_pct = pos.unrealized_pnl_pct(price)
            total_pnl += pnl
            snap = {"key": key, "symbol": pos.symbol, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 4)}
            snaps.append(snap)

            if pnl_pct <= self.POS_CRIT:
                self._alert("CRITICAL", f"{pos.symbol} down {pnl_pct:.1%}", snap)
                recs.append({"action": "EMERGENCY_EXIT", "symbol": pos.symbol, "priority": "HIGH"})
            elif pnl_pct <= self.POS_WARN:
                self._alert("WARNING", f"{pos.symbol} down {pnl_pct:.1%}", snap)

            if pos.direction == "LONG" and pos.stop_loss > 0:
                dist = (price - pos.stop_loss) / price
                if 0 < dist < 0.01:
                    self._alert("WARNING", f"{pos.symbol} within 1% of stop", snap)

        if self.sod > 0:
            daily = total_pnl / self.sod
            if daily <= self.PORT_CRIT:
                self._alert("CRITICAL", f"Portfolio down {daily:.1%} today", {})
                recs.append({"action": "REDUCE_ALL", "priority": "HIGH"})
            elif daily <= self.PORT_WARN:
                self._alert("WARNING", f"Portfolio down {daily:.1%} today", {})

        if vix and vix > 30:
            self._alert("CRITICAL", f"VIX at {vix:.1f} - panic", {"vix": vix})
            recs.append({"action": "REDUCE_ALL", "priority": "HIGH"})

        if broker_eq is not None:
            disc = abs(broker_eq - self.equity)
            if disc > self.equity * 0.02:
                self._alert("WARNING", f"Equity mismatch: local=${self.equity:,.0f} broker=${broker_eq:,.0f}", {})

        crits = [a for a in self.alerts if a["level"] == "CRITICAL"]
        if crits:
            logger.warning(f"MONITOR: {len(crits)} CRITICAL alerts")
        return {"alerts": self.alerts, "recommendations": recs, "positions": snaps, "total_pnl": round(total_pnl, 2)}

    def _alert(self, level, msg, data):
        self.alerts.append({"level": level, "message": msg, "data": data, "timestamp": datetime.utcnow().isoformat()})
