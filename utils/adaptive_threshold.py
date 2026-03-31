"""
#14 - Adaptive Entry Threshold.
Adjusts the 70-point threshold based on actual results.
"""

import json
from pathlib import Path
from loguru import logger


class AdaptiveThreshold:

    CONFIG_PATH = "config/adaptive_threshold.json"
    DEFAULT = 70
    MIN_THRESHOLD = 60
    MAX_THRESHOLD = 85
    MIN_TRADES = 30

    def __init__(self):
        self.config = self._load()

    def _load(self):
        if Path(self.CONFIG_PATH).exists():
            with open(self.CONFIG_PATH) as f:
                return json.load(f)
        return {
            "current_threshold": self.DEFAULT,
            "history": [],
        }

    def _save(self):
        Path(self.CONFIG_PATH).parent.mkdir(
            parents=True, exist_ok=True
        )
        with open(self.CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)

    @property
    def threshold(self):
        return self.config.get(
            "current_threshold", self.DEFAULT
        )

    def recalibrate(self, trade_log):
        """
        Analyze trades and adjust threshold.
        Call weekly after model training.
        """
        if not trade_log or len(trade_log) < self.MIN_TRADES:
            logger.info(
                f"Need {self.MIN_TRADES}+ trades "
                f"to calibrate (have {len(trade_log)})"
            )
            return self.threshold

        # Group by score ranges
        ranges = {}
        for t in trade_log:
            score = t.get("signal_score", 50)
            bucket = int(score // 5) * 5
            if bucket not in ranges:
                ranges[bucket] = {"wins": 0, "total": 0}
            ranges[bucket]["total"] += 1
            if t.get("profitable"):
                ranges[bucket]["wins"] += 1

        # Find lowest score range with >55% win rate
        best = self.DEFAULT
        for bucket in sorted(ranges.keys(), reverse=True):
            data = ranges[bucket]
            if data["total"] < 5:
                continue
            wr = data["wins"] / data["total"]
            if wr >= 0.55:
                best = bucket
            else:
                break

        new_t = max(
            self.MIN_THRESHOLD,
            min(self.MAX_THRESHOLD, best),
        )

        old_t = self.threshold
        self.config["current_threshold"] = new_t
        self.config["history"].append({
            "old": old_t,
            "new": new_t,
            "trades_analyzed": len(trade_log),
            "ranges": {
                str(k): {
                    "wr": round(
                        v["wins"] / max(v["total"], 1), 2
                    ),
                    "n": v["total"],
                }
                for k, v in sorted(ranges.items())
            },
        })
        self._save()

        if new_t != old_t:
            logger.info(
                f"Threshold adjusted: {old_t} -> {new_t}"
            )
        else:
            logger.info(f"Threshold unchanged at {new_t}")

        return new_t
