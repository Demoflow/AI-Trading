"""
Flow Persistence Tracker.
Tracks unusual options flow over multiple days.
3 consecutive days of aligned flow = 2x conviction boost.
Stores flow history for pattern recognition.
"""
import os
import json
from datetime import date, timedelta
from loguru import logger

FLOW_HISTORY_PATH = "config/flow_history.json"


class FlowPersistence:

    def __init__(self):
        self._load_history()

    def _load_history(self):
        try:
            self.history = json.load(open(FLOW_HISTORY_PATH))
        except (FileNotFoundError, json.JSONDecodeError):
            self.history = {}

    def _save_history(self):
        # Keep only last 10 days
        cutoff = (date.today() - timedelta(days=10)).isoformat()
        cleaned = {}
        for sym, days in self.history.items():
            cleaned[sym] = {d: v for d, v in days.items() if d >= cutoff}
            if not cleaned[sym]:
                continue
        self.history = cleaned
        json.dump(self.history, open(FLOW_HISTORY_PATH, "w"), indent=2)

    def record_signal(self, symbol, direction, strength, cp_ratio, premium):
        """Record today's flow signal for a symbol."""
        today = date.today().isoformat()
        if symbol not in self.history:
            self.history[symbol] = {}
        self.history[symbol][today] = {
            "direction": direction,
            "strength": strength,
            "cp_ratio": round(cp_ratio, 2),
            "premium": premium,
        }
        self._save_history()

    def get_persistence(self, symbol, direction):
        """
        Check how many consecutive days this symbol has had
        aligned flow in the same direction.
        Returns: {
            "consecutive_days": int,
            "total_days_5d": int,
            "avg_strength": float,
            "persistence_boost": int (0-20 conviction points),
            "is_persistent": bool,
        }
        """
        if symbol not in self.history:
            return {
                "consecutive_days": 0, "total_days_5d": 0,
                "avg_strength": 0, "persistence_boost": 0,
                "is_persistent": False,
            }

        days_data = self.history[symbol]
        today = date.today()

        # Count consecutive days of aligned flow (backwards from yesterday)
        consecutive = 0
        total_aligned = 0
        strengths = []

        for i in range(1, 6):  # Check last 5 trading days
            check_date = today - timedelta(days=i)
            # Skip weekends
            if check_date.weekday() >= 5:
                continue
            ds = check_date.isoformat()
            if ds in days_data:
                day_flow = days_data[ds]
                if day_flow["direction"] == direction:
                    if i <= 3 or consecutive > 0:
                        consecutive += 1
                    total_aligned += 1
                    strengths.append(day_flow["strength"])
                else:
                    if consecutive > 0:
                        break  # Streak broken

        avg_str = sum(strengths) / len(strengths) if strengths else 0

        # Calculate boost
        boost = 0
        if consecutive >= 3:
            boost = 20  # 3+ days = strong persistence
        elif consecutive >= 2:
            boost = 12  # 2 days = moderate persistence
        elif total_aligned >= 3:
            boost = 8   # 3 of 5 days aligned
        elif total_aligned >= 2:
            boost = 4   # 2 of 5 days aligned

        return {
            "consecutive_days": consecutive,
            "total_days_5d": total_aligned,
            "avg_strength": round(avg_str, 1),
            "persistence_boost": boost,
            "is_persistent": consecutive >= 2,
        }

    def record_all_signals(self, flow_results):
        """Record all signals from tonight's scan."""
        count = 0
        for flow in flow_results:
            self.record_signal(
                flow["symbol"],
                flow["direction"],
                flow.get("strength", 0),
                flow.get("cp_ratio", 1.0),
                flow.get("premium_total", 0),
            )
            count += 1
        logger.info(f"Flow persistence: recorded {count} signals for {date.today()}")
