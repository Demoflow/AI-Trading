"""
Greeks Optimizer.
Selects options with best delta-to-theta ratio.
Maximizes directional exposure per dollar of time decay.
"""
from loguru import logger


class GreeksOptimizer:

    @staticmethod
    def score_contract(contract):
        """
        Score a contract by delta-to-theta efficiency.
        Higher = better bang for your theta buck.
        """
        delta = abs(contract.get("delta", 0))
        theta = abs(contract.get("theta", 0.01))
        gamma = abs(contract.get("gamma", 0))
        mid = contract.get("mid", 0)

        if theta <= 0 or mid <= 0:
            return 0

        # Delta-theta ratio: how much delta per dollar of daily decay
        dt_ratio = delta / theta

        # Cost efficiency: delta per dollar invested
        cost_eff = delta / mid if mid > 0 else 0

        # Gamma bonus: higher gamma = more convexity
        gamma_bonus = gamma * 100

        # Combined score
        return round(dt_ratio * 0.5 + cost_eff * 30 + gamma_bonus * 20, 2)

    @staticmethod
    def filter_best(contracts, top_n=3):
        """Return top N contracts by Greeks score."""
        scored = []
        for c in contracts:
            score = GreeksOptimizer.score_contract(c)
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_n]]
