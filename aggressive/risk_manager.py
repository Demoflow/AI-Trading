"""
Risk Manager.
- Weekend position reduction
- Late-day scan scheduling
- Flow accuracy tracking
- Daily notifications
"""

import os
import json
from datetime import datetime, date
from loguru import logger


class RiskManager:

    MAX_WEEKEND_POSITIONS = 3
    MAX_WEEKEND_DEPLOYMENT = 0.30

    def should_reduce_for_weekend(self, positions, equity):
        """Check if we need to reduce before weekend."""
        now = datetime.now()
        # Friday after 2:00 PM
        if now.weekday() != 4 or now.hour < 14:
            return False, []

        open_pos = [p for p in positions if p.get("status") == "OPEN"]
        if len(open_pos) <= self.MAX_WEEKEND_POSITIONS:
            return False, []

        # Sort by P&L, close worst performers
        scored = []
        for p in open_pos:
            pnl = p.get("unrealized_pnl", 0)
            scored.append((pnl, p))
        scored.sort(key=lambda x: x[0])

        to_close = []
        while len(scored) > self.MAX_WEEKEND_POSITIONS:
            _, pos = scored.pop(0)
            to_close.append(pos)

        if to_close:
            logger.info(
                f"WEEKEND RISK: Closing {len(to_close)} "
                f"positions before weekend"
            )
        return True, to_close


class FlowTracker:
    """Track flow signal accuracy over time."""

    def __init__(self):
        self.path = "config/flow_accuracy.json"
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                return json.load(f)
        return {"signals": [], "stats": {}}

    def _save(self):
        os.makedirs("config", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    def record_signal(self, symbol, direction, strength, score):
        self.data["signals"].append({
            "date": date.today().isoformat(),
            "symbol": symbol,
            "direction": direction,
            "strength": strength,
            "score": score,
            "outcome": None,
        })
        self._save()

    def record_outcome(self, symbol, pnl):
        for s in reversed(self.data["signals"]):
            if s["symbol"] == symbol and s["outcome"] is None:
                s["outcome"] = "WIN" if pnl > 0 else "LOSS"
                s["pnl"] = round(pnl, 2)
                break
        self._update_stats()
        self._save()

    def _update_stats(self):
        completed = [s for s in self.data["signals"] if s["outcome"]]
        if not completed:
            return

        by_strength = {}
        for s in completed:
            st = s["strength"]
            if st not in by_strength:
                by_strength[st] = {"wins": 0, "total": 0, "pnl": 0}
            by_strength[st]["total"] += 1
            if s["outcome"] == "WIN":
                by_strength[st]["wins"] += 1
            by_strength[st]["pnl"] += s.get("pnl", 0)

        self.data["stats"] = {
            str(k): {
                "win_rate": round(v["wins"] / max(v["total"], 1), 2),
                "total": v["total"],
                "pnl": round(v["pnl"], 2),
            }
            for k, v in by_strength.items()
        }

    def get_strength_modifier(self, strength):
        """Adjust sizing based on historical accuracy."""
        stats = self.data.get("stats", {})
        key = str(strength)
        if key in stats and stats[key]["total"] >= 10:
            wr = stats[key]["win_rate"]
            if wr > 0.65:
                return 1.15
            elif wr < 0.40:
                return 0.80
        return 1.0

    def print_stats(self):
        stats = self.data.get("stats", {})
        if not stats:
            print("  No flow accuracy data yet.")
            return
        print("  Flow Signal Accuracy:")
        for s, d in sorted(stats.items()):
            print(
                f"    Strength {s}: "
                f"WR={d['win_rate']:.0%} "
                f"({d['total']} trades) "
                f"P&L=${d['pnl']:+,.2f}"
            )
