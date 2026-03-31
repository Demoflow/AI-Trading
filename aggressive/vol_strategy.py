"""
Volatility Regime Strategy Selector.
Adjusts strategy selection based on VIX level and direction.
At VIX extremes, pure volatility trades have better EV than directional.
"""
from loguru import logger


class VolatilityStrategySelector:

    @staticmethod
    def get_regime(vix, vix_5d_ago=None):
        """
        Classify the volatility regime.
        Returns: regime dict with strategy bias and modifiers.
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
                "strategy_bias": "BUY_VOLATILITY",
                "preferred_strategies": ["STRADDLE_BUY", "STRANGLE_BUY", "RATIO_BACKSPREAD"],
                "avoid_strategies": ["NAKED_LONG", "CREDIT_SPREAD"],
                "size_modifier": 0.75,  # Smaller size - waiting for vol expansion
                "description": "VIX extremely low. Buy volatility - expansion imminent.",
            }
        elif vix < 18:
            return {
                "regime": "LOW_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "DIRECTIONAL_CHEAP",
                "preferred_strategies": ["NAKED_LONG", "RISK_REVERSAL", "DIAGONAL_SPREAD"],
                "avoid_strategies": ["CREDIT_SPREAD"],
                "size_modifier": 1.0,
                "description": "Low VIX. Options are cheap. Buy directional.",
            }
        elif vix < 25:
            return {
                "regime": "NORMAL_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "BALANCED",
                "preferred_strategies": ["DEBIT_SPREAD", "DIAGONAL_SPREAD", "RISK_REVERSAL"],
                "avoid_strategies": [],
                "size_modifier": 1.0,
                "description": "Normal VIX. All strategies available.",
            }
        elif vix < 32:
            return {
                "regime": "HIGH_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "SELL_PREMIUM",
                "preferred_strategies": ["DEBIT_SPREAD", "CREDIT_SPREAD", "SHORT_STRANGLE"],
                "avoid_strategies": ["NAKED_LONG"],
                "size_modifier": 0.85,
                "description": "High VIX. Options expensive. Use spreads, sell premium.",
            }
        else:
            return {
                "regime": "EXTREME_HIGH_VOL",
                "vix": vix,
                "direction": vix_direction,
                "strategy_bias": "SELL_VOLATILITY",
                "preferred_strategies": ["CREDIT_SPREAD", "SHORT_STRANGLE", "NAKED_PUT"],
                "avoid_strategies": ["NAKED_LONG", "STRADDLE_BUY"],
                "size_modifier": 0.70,
                "description": "VIX extreme. Sell premium. Use defined-risk spreads only.",
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
