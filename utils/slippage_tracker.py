"""
Slippage Tracker - Records intended vs actual fill prices.
"""

import json
from datetime import datetime
from pathlib import Path
from loguru import logger


class SlippageTracker:

    LOG_PATH = "config/slippage_log.json"

    def __init__(self):
        self.records = self._load()

    def _load(self):
        if Path(self.LOG_PATH).exists():
            with open(self.LOG_PATH) as f:
                return json.load(f)
        return []

    def _save(self):
        Path(self.LOG_PATH).parent.mkdir(
            parents=True, exist_ok=True
        )
        with open(self.LOG_PATH, "w") as f:
            json.dump(
                self.records[-500:], f,
                indent=2, default=str
            )

    def record(self, symbol, side, intended_price,
               fill_price, quantity):
        slip = fill_price - intended_price
        slip_pct = slip / intended_price if intended_price > 0 else 0
        if side == "BUY":
            cost_impact = slip * quantity
        else:
            cost_impact = -slip * quantity

        rec = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "side": side,
            "intended": round(intended_price, 4),
            "filled": round(fill_price, 4),
            "slippage": round(slip, 4),
            "slippage_pct": round(slip_pct, 6),
            "cost_impact": round(cost_impact, 2),
            "quantity": quantity,
        }
        self.records.append(rec)
        self._save()

        if abs(slip_pct) > 0.002:
            logger.warning(
                f"HIGH SLIPPAGE: {symbol} {side} "
                f"intended ${intended_price:.2f} "
                f"filled ${fill_price:.2f} "
                f"({slip_pct:+.3%})"
            )

    def get_avg_slippage(self, side=None, last_n=50):
        recs = self.records[-last_n:]
        if side:
            recs = [r for r in recs if r["side"] == side]
        if not recs:
            return 0
        return sum(
            abs(r["slippage_pct"]) for r in recs
        ) / len(recs)

    def get_total_cost(self):
        return sum(r["cost_impact"] for r in self.records)
