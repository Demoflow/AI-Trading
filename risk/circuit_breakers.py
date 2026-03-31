"""
Independent Circuit Breaker System.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from loguru import logger


class IndependentCircuitBreakers:

    INTRADAY = -0.028
    ROLL_3D = -0.045
    ROLL_5D = -0.055
    BLEED_DAYS = 5
    BLEED_TOTAL = -0.03
    MAX_DD = -0.13
    CONSEC_LOSS = 7

    def __init__(self):
        self.path = Path("config/breaker_state.json")
        self.state = self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path) as f:
                return json.load(f)
        return {"halted": False, "halt_reason": None, "halt_until": None, "peak": 0, "daily_pnl": [], "consec_losses": 0}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

    def update_and_check(self, equity, intraday_pnl=0, last_profitable=None):
        result = {"halted": False, "reason": None, "action": None}

        if self.state.get("halted"):
            hu = self.state.get("halt_until")
            if hu and date.today() > date.fromisoformat(hu):
                self.state["halted"] = False
                self.state["halt_reason"] = None
            else:
                result["halted"] = True
                result["reason"] = self.state["halt_reason"]
                return result

        if equity > self.state.get("peak", 0):
            self.state["peak"] = equity

        hist = self.state.get("daily_pnl", [])
        today = date.today().isoformat()
        if hist and hist[-1].get("date") == today:
            hist[-1]["pnl"] = intraday_pnl
            hist[-1]["eq"] = equity
        else:
            hist.append({"date": today, "pnl": intraday_pnl, "eq": equity})
        self.state["daily_pnl"] = hist[-30:]

        if last_profitable is not None:
            if last_profitable:
                self.state["consec_losses"] = 0
            else:
                self.state["consec_losses"] = self.state.get("consec_losses", 0) + 1

        if intraday_pnl <= self.INTRADAY:
            return self._halt(f"Intraday loss {intraday_pnl:.1%}", 1)
        if len(hist) >= 3:
            r3 = sum(h["pnl"] for h in hist[-3:])
            if r3 <= self.ROLL_3D:
                return self._halt(f"3-day loss {r3:.1%}", 1)
        if len(hist) >= 5:
            r5 = sum(h["pnl"] for h in hist[-5:])
            if r5 <= self.ROLL_5D:
                return self._halt(f"5-day loss {r5:.1%}", 2)
        if len(hist) >= self.BLEED_DAYS:
            rec = hist[-self.BLEED_DAYS:]
            if all(h["pnl"] < 0 for h in rec):
                tot = sum(h["pnl"] for h in rec)
                if tot <= self.BLEED_TOTAL:
                    return self._halt(f"Slow bleed: {self.BLEED_DAYS} days, {tot:.1%}", 2)
        peak = self.state.get("peak", equity)
        if peak > 0:
            dd = (equity - peak) / peak
            if dd <= self.MAX_DD:
                return self._halt(f"Drawdown {dd:.1%}", 5)
        cl = self.state.get("consec_losses", 0)
        if cl >= self.CONSEC_LOSS:
            return self._halt(f"{cl} consecutive losses", 3)

        self._save()
        return result

    def _halt(self, reason, days):
        hu = (date.today() + timedelta(days=days)).isoformat()
        self.state["halted"] = True
        self.state["halt_reason"] = reason
        self.state["halt_until"] = hu
        self._save()
        logger.warning(f"INDEPENDENT BREAKER: {reason} (halt until {hu})")
        return {"halted": True, "reason": reason, "halt_until": hu, "action": "HALT"}
