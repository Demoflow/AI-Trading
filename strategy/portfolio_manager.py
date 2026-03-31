"""
Portfolio Manager - Updated.
#9: Signal-quality position sizing.
#10: Max 3 stocks (not 4), larger per position.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from loguru import logger
from strategy.position_tracker import PositionTracker
from strategy.day_trade_tracker import DayTradeTracker


class PortfolioManager:

    MAX_OPT_PCT = 0.40
    MAX_STK_PCT = 0.40
    MAX_ETF_PCT = 0.25
    MAX_TOTAL_PCT = 0.70
    MAX_OPT_POS = 3
    MAX_STK_POS = 3
    MAX_ETF_POS = 2
    MAX_SECTOR = 2
    DAILY_LIMIT = 0.03
    WEEKLY_LIMIT = 0.06
    MAX_DD = 0.15

    def __init__(self, equity):
        self.equity = equity
        self.peak = self._load_peak()
        self.tracker = PositionTracker()
        self.dt_tracker = DayTradeTracker()
        self.pnl_log = self._load_pnl()
        self.halted = False
        self.halt_reason = None
        self.halt_until = None

    def _load_peak(self):
        p = Path("config/peak_equity.json")
        if p.exists():
            with open(p) as f:
                return json.load(f).get("peak", self.equity)
        return self.equity

    def _save_peak(self):
        if self.equity > self.peak:
            self.peak = self.equity
        with open("config/peak_equity.json", "w") as f:
            json.dump({"peak": self.peak}, f)

    def _load_pnl(self):
        p = Path("config/daily_pnl.json")
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return []

    def _save_pnl(self):
        with open("config/daily_pnl.json", "w") as f:
            json.dump(
                self.pnl_log[-90:], f,
                indent=2, default=str,
            )

    def update_equity(self, new_eq):
        old = self.equity
        self.equity = new_eq
        self._save_peak()
        change = (new_eq - old) / old if old > 0 else 0
        self.pnl_log.append({
            "date": date.today().isoformat(),
            "equity": new_eq,
            "daily_pnl_pct": round(change, 4),
        })
        self._save_pnl()
        self._check_breakers()

    def _check_breakers(self):
        today_str = date.today().isoformat()
        te = [
            e for e in self.pnl_log
            if e["date"] == today_str
        ]
        if te:
            dl = sum(e["daily_pnl_pct"] for e in te)
            if dl <= -self.DAILY_LIMIT:
                self.halted = True
                self.halt_reason = f"Daily loss {dl:.1%}"
                self.halt_until = (
                    date.today() + timedelta(days=1)
                )
                return
        wa = (date.today() - timedelta(days=7)).isoformat()
        weekly = [
            e for e in self.pnl_log
            if e.get("date", "") >= wa
        ]
        if weekly:
            wl = sum(e["daily_pnl_pct"] for e in weekly)
            if wl <= -self.WEEKLY_LIMIT:
                self.halted = True
                self.halt_reason = f"Weekly loss {wl:.1%}"
                self.halt_until = (
                    date.today() + timedelta(days=2)
                )
                return
        if self.peak > 0:
            dd = (self.peak - self.equity) / self.peak
            if dd >= self.MAX_DD:
                self.halted = True
                self.halt_reason = f"Max drawdown {dd:.1%}"
                self.halt_until = (
                    date.today() + timedelta(days=7)
                )
                return
        if self.halt_until and date.today() >= self.halt_until:
            self.halted = False
            self.halt_reason = None
            self.halt_until = None

    def can_enter(self, instrument, sector, cost):
        if self.halted:
            return False, f"Halted: {self.halt_reason}"
        if instrument == "ETF" and date.today().weekday() == 4:
            return False, "No ETFs on Friday"
        dep = self.tracker.total_deployed()
        new_pct = (dep + cost) / self.equity if self.equity > 0 else 1
        if new_pct > self.MAX_TOTAL_PCT:
            return False, f"Exceeds {self.MAX_TOTAL_PCT:.0%}"
        if instrument in ("CALL", "PUT"):
            cur = (
                self.tracker.by_instrument("CALL")
                + self.tracker.by_instrument("PUT")
            )
            if len(cur) >= self.MAX_OPT_POS:
                return False, f"Max options ({self.MAX_OPT_POS})"
            od = sum(p.entry_cost for p in cur)
            if (od + cost) / self.equity > self.MAX_OPT_PCT:
                return False, "Exceeds options alloc"
        elif instrument == "STOCK":
            cur = self.tracker.by_instrument("STOCK")
            if len(cur) >= self.MAX_STK_POS:
                return False, f"Max stocks ({self.MAX_STK_POS})"
            sd = sum(p.entry_cost for p in cur)
            if (sd + cost) / self.equity > self.MAX_STK_PCT:
                return False, "Exceeds stock alloc"
        elif instrument == "ETF":
            cur = self.tracker.by_instrument("ETF")
            if len(cur) >= self.MAX_ETF_POS:
                return False, f"Max ETFs ({self.MAX_ETF_POS})"
            ed = sum(p.entry_cost for p in cur)
            if (ed + cost) / self.equity > self.MAX_ETF_PCT:
                return False, "Exceeds ETF alloc"
        if instrument == "STOCK" and sector:
            sp = self.tracker.by_sector(sector)
            if len(sp) >= self.MAX_SECTOR:
                return False, f"Max in {sector}"
        return True, "APPROVED"

    def get_size(self, instrument, price, atr, score=50, modifier=1.0):
        """
        #9: Signal-quality based sizing.
        70-75 score = 1.0% risk
        75-85 score = 1.5% risk
        85+   score = 2.0% risk
        """
        eq = self.equity
        if score >= 85:
            risk_pct = 0.020
        elif score >= 75:
            risk_pct = 0.015
        else:
            risk_pct = 0.010

        if instrument in ("CALL", "PUT"):
            mc = eq * 0.08 * modifier
            return {
                "max_cost": round(mc, 2),
                "sizing_method": "fixed_dollar_options",
                "risk_pct": risk_pct,
            }
        elif instrument == "STOCK":
            risk = eq * risk_pct
            mx = eq * 0.12 * modifier
            stop_dist = atr
            if stop_dist <= 0:
                stop_dist = price * 0.03
            shares = int(risk / stop_dist)
            cost = shares * price
            if cost > mx:
                shares = int(mx / price)
                cost = shares * price
            return {
                "shares": shares,
                "cost": round(cost, 2),
                "risk_amount": round(risk, 2),
                "risk_pct": risk_pct,
            }
        elif instrument == "ETF":
            mx = eq * 0.10 * modifier
            shares = int(mx / price) if price > 0 else 0
            return {
                "shares": shares,
                "cost": round(shares * price, 2),
            }
        return {"shares": 0, "cost": 0}
