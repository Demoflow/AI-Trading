"""
Volatility Regime Strategy Selector — Cash Account Mode.
Only NAKED_LONG is permitted. All premium-selling and spread strategies
are blocked. Score adjustments only apply to NAKED_LONG.
"""
from loguru import logger


class VolatilityStrategySelector:

    @staticmethod
    def get_regime(vix, vix_5d_ago=None):
        """
        Classify the volatility regime.
        Returns: regime dict with strategy bias and modifiers.
        Cash account: NAKED_LONG is always preferred, size shrinks with VIX.
        """
        if vix_5d_ago:
            vix_direction = "RISING" if vix > vix_5d_ago * 1.10 else (
                "FALLING" if vix < vix_5d_ago * 0.90 else "STABLE"
            )
        else:
            vix_direction = "STABLE"

        if vix < 13:
            return {
                "regime": "EXTREME_LOW_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "NAKED_LONG",
                "preferred_strategies": ["NAKED_LONG"],
                "avoid_strategies": [],
                "size_modifier": 0.75,
                "description": "VIX extremely low. Options cheap, buy directional.",
            }
        elif vix < 18:
            return {
                "regime": "LOW_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "NAKED_LONG",
                "preferred_strategies": ["NAKED_LONG"],
                "avoid_strategies": [],
                "size_modifier": 1.0,
                "description": "Low VIX. Options cheap. Buy directional.",
            }
        elif vix < 25:
            return {
                "regime": "NORMAL_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "NAKED_LONG",
                "preferred_strategies": ["NAKED_LONG"],
                "avoid_strategies": [],
                "size_modifier": 1.0,
                "description": "Normal VIX. Buy directional with full size.",
            }
        elif vix < 32:
            return {
                "regime": "HIGH_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "NAKED_LONG",
                "preferred_strategies": ["NAKED_LONG"],
                "avoid_strategies": [],
                "size_modifier": 0.80,
                "description": "High VIX. Options expensive — reduce size, buy puts on weakness.",
            }
        else:
            return {
                "regime": "EXTREME_HIGH_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "NAKED_LONG",
                "preferred_strategies": ["NAKED_LONG"],
                "avoid_strategies": [],
                "size_modifier": 0.60,
                "description": "VIX extreme. Reduce size significantly, only highest-conviction puts.",
            }

    @staticmethod
    def adjust_strategy_score(strategy_type, vol_regime):
        """
        Adjust a strategy's score based on volatility regime.
        Returns: score adjustment (-20 to +20)
        """
        preferred = vol_regime.get("preferred_strategies", [])
        avoid = vol_regime.get("avoid_strategies", [])

        if strategy_type in preferred:
            return 15
        elif strategy_type in avoid:
            return -15
        return 0

    @staticmethod
    def should_trade_volatility(vix, vix_percentile=None):
        """
        Determine if a pure volatility trade (straddle/strangle)
        has better EV than directional trades.
        Returns: (should_trade_vol, direction, confidence)
        """
        if vix < 13:
            return True, "BUY_VOL", 80
        elif vix > 35:
            return True, "SELL_VOL", 75
        elif vix_percentile and vix_percentile < 10:
            return True, "BUY_VOL", 70
        elif vix_percentile and vix_percentile > 90:
            return True, "SELL_VOL", 70
        return False, "NONE", 0
