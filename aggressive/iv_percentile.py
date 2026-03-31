"""
IV Percentile Calculator.
More accurate than IV rank for detecting cheap/expensive options.
IV Rank: where current IV sits between 52-week high/low.
IV Percentile: what % of days had lower IV than today.
"""
from loguru import logger


class IVPercentile:

    @staticmethod
    def calculate(iv_history, current_iv):
        """
        Calculate IV percentile from historical data.
        iv_history: list of daily IV values (252 trading days ideal)
        current_iv: today's IV
        Returns: percentile (0-100)
        """
        if not iv_history or current_iv <= 0:
            return 50  # Default to neutral

        below = sum(1 for iv in iv_history if iv < current_iv)
        return round(below / len(iv_history) * 100, 1)

    @staticmethod
    def get_strategy_bias(iv_percentile):
        """
        Returns strategy bias based on IV percentile.
        Low IV percentile = buy options (cheap)
        High IV percentile = sell premium or use spreads
        """
        if iv_percentile < 25:
            return "BUY_NAKED"  # Options are cheap
        elif iv_percentile < 50:
            return "BUY_SLIGHT_SPREAD"  # Slightly elevated, lean spreads
        elif iv_percentile < 75:
            return "SPREAD_PREFERRED"  # Elevated, use spreads
        else:
            return "SELL_PREMIUM"  # Expensive, sell premium
