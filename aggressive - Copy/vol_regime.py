"""
Volatility Regime Classifier.
Tracks VIX direction, not just level.
VIX 25 falling from 30 = bullish (recovery)
VIX 25 rising from 20 = bearish (fear increasing)
"""

import json
import os
from datetime import date
from loguru import logger


class VolRegime:

    def __init__(self):
        self.history = self._load()

    def _load(self):
        path = "config/vix_history.json"
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"readings": []}

    def _save(self):
        os.makedirs("config", exist_ok=True)
        with open("config/vix_history.json", "w") as f:
            json.dump(self.history, f, indent=2)

    def record(self, vix):
        """Record today's VIX reading."""
        today = date.today().isoformat()
        readings = self.history["readings"]

        # Don't duplicate
        if readings and readings[-1]["date"] == today:
            readings[-1]["vix"] = vix
        else:
            readings.append({"date": today, "vix": round(vix, 2)})

        # Keep last 30 readings
        self.history["readings"] = readings[-30:]
        self._save()

    def classify(self, current_vix):
        """
        Classify the volatility regime.
        Returns:
            regime: {
                level: "LOW" / "MODERATE" / "HIGH" / "EXTREME"
                trend: "RISING" / "FALLING" / "STABLE"
                signal: combined assessment
                strategy_bias: which strategies benefit
                size_modifier: position sizing adjustment
            }
        """
        self.record(current_vix)

        readings = self.history["readings"]

        # Level classification
        if current_vix < 15:
            level = "LOW"
        elif current_vix < 20:
            level = "MODERATE"
        elif current_vix < 30:
            level = "HIGH"
        else:
            level = "EXTREME"

        # Trend: compare to 3-day and 5-day ago
        trend = "STABLE"
        if len(readings) >= 3:
            vix_3d = readings[-3]["vix"]
            change_3d = current_vix - vix_3d
            if change_3d > 2:
                trend = "RISING"
            elif change_3d < -2:
                trend = "FALLING"

        # Combined signal
        if level == "HIGH" and trend == "RISING":
            signal = "FEAR_INCREASING"
            strategy_bias = "CREDIT_SPREAD"  # Sell rich premium
            size_mod = 0.70
        elif level == "HIGH" and trend == "FALLING":
            signal = "FEAR_RECEDING"
            strategy_bias = "DEBIT_SPREAD"  # IV still high but falling
            size_mod = 0.90
        elif level == "EXTREME":
            signal = "PANIC"
            strategy_bias = "CREDIT_SPREAD"  # Premium extremely rich
            size_mod = 0.50
        elif level == "LOW" and trend == "STABLE":
            signal = "COMPLACENT"
            strategy_bias = "NAKED_LONG"  # Cheap options
            size_mod = 1.15
        elif level == "MODERATE" and trend == "FALLING":
            signal = "NORMALIZING"
            strategy_bias = "DEBIT_SPREAD"
            size_mod = 1.05
        else:
            signal = "NEUTRAL"
            strategy_bias = "ANY"
            size_mod = 1.0

        regime = {
            "level": level,
            "trend": trend,
            "signal": signal,
            "strategy_bias": strategy_bias,
            "size_modifier": round(size_mod, 2),
            "current_vix": round(current_vix, 2),
        }

        logger.info(
            f"Vol Regime: VIX {current_vix:.1f} "
            f"{level}/{trend} = {signal} "
            f"(bias: {strategy_bias}, size: {size_mod:.0%})"
        )

        return regime
