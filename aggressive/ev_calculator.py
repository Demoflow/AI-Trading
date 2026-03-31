"""
Expected Value (EV) Calculator.
Calculates the probability-weighted expected dollar
return of each trade BEFORE entry.

Uses:
- Delta as probability proxy
- IV for expected move calculation
- Strategy structure for payoff mapping
- Historical win rate by flow strength (if available)

Only takes trades with positive EV.
"""

import math
import json
import os
from loguru import logger


class EVCalculator:

    MIN_EV_RATIO = 0.05  # Minimum EV as % of cost (5%)

    def __init__(self):
        self.flow_stats = self._load_flow_stats()

    def _load_flow_stats(self):
        path = "config/flow_accuracy.json"
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                return data.get("stats", {})
            except Exception:
                pass
        return {}

    def calculate_ev(self, strategy, conviction, flow_strength,
                     iv_rank, direction, gex_regime=None):
        """
        Calculate expected value for a strategy.
        Returns:
            ev_result: {
                ev_dollar: expected dollar P&L,
                ev_ratio: EV as % of cost,
                prob_profit: estimated probability of profit,
                prob_max: probability of max profit,
                risk_reward: reward/risk ratio,
                kelly_fraction: optimal bet sizing (Kelly),
                grade: A/B/C/D/F,
                is_positive: True if EV > 0,
            }
        """
        stype = strategy.get("type", "NAKED_LONG")
        cost = strategy.get("total_cost", 0)
        max_profit = strategy.get("max_profit", 0)
        max_loss = strategy.get("max_loss", 0)

        if cost <= 0:
            return None

        # Handle "unlimited" max profit
        if isinstance(max_profit, str):
            # For naked longs, estimate max profit as 2x cost
            max_profit = cost * 2 / 100
        if isinstance(max_loss, str):
            max_loss = cost / 100

        # ── PROBABILITY ESTIMATION ──

        # Base probability from delta (contracts)
        contracts = strategy.get("contracts", [])
        if contracts:
            long_leg = next(
                (c for c in contracts if c.get("leg") == "LONG"),
                contracts[0]
            )
            delta = abs(long_leg.get("delta", 0.50))
        else:
            delta = 0.50

        # Start with delta as base probability
        base_prob = delta

        # Adjust for conviction score
        if conviction >= 90:
            prob_adjust = 0.10
        elif conviction >= 85:
            prob_adjust = 0.06
        elif conviction >= 80:
            prob_adjust = 0.03
        else:
            prob_adjust = 0

        # Adjust for flow strength
        flow_adjust = 0
        if flow_strength >= 6:
            flow_adjust = 0.08
        elif flow_strength >= 5:
            flow_adjust = 0.05
        elif flow_strength >= 4:
            flow_adjust = 0.03

        # Adjust for historical flow accuracy
        hist_adjust = 0
        str_key = str(flow_strength)
        if str_key in self.flow_stats:
            stats = self.flow_stats[str_key]
            if stats.get("total", 0) >= 10:
                hist_wr = stats["win_rate"]
                hist_adjust = (hist_wr - 0.50) * 0.5

        # GEX regime adjustment
        gex_adjust = 0
        if gex_regime == "POSITIVE":
            # Positive GEX = mean-reverting, spreads work better
            if stype in ("DEBIT_SPREAD", "CREDIT_SPREAD", "BROKEN_WING_BUTTERFLY"):
                gex_adjust = 0.04
        elif gex_regime == "NEGATIVE":
            # Negative GEX = trending, directional plays work
            if stype == "NAKED_LONG":
                gex_adjust = 0.04

        # IV rank adjustment
        iv_adjust = 0
        if iv_rank < 30:
            if stype == "NAKED_LONG":
                iv_adjust = 0.03  # Cheap options, good for longs
        elif iv_rank > 60:
            if stype in ("CREDIT_SPREAD", "BROKEN_WING_BUTTERFLY"):
                iv_adjust = 0.04  # High IV good for selling

        # Final probability
        prob_profit = min(0.85, max(0.25, (
            base_prob + prob_adjust + flow_adjust +
            hist_adjust + gex_adjust + iv_adjust
        )))
        prob_loss = 1 - prob_profit

        # ── STRATEGY-SPECIFIC EV ──

        if stype == "NAKED_LONG":
            # Can make anywhere from 0 to unlimited
            # Use average expected gain based on targets
            avg_win = max_profit * 0.65  # Average win is 65% of max
            avg_loss = max_loss * 0.75  # Average loss is 75% of max
            ev = (prob_profit * avg_win * 100) - (prob_loss * avg_loss * 100)

        elif stype == "DEBIT_SPREAD":
            # Max profit and loss are defined
            avg_win = max_profit * 0.60  # Often don't reach full value
            avg_loss = max_loss * 0.80  # Often stopped before max loss
            ev = (prob_profit * avg_win * 100) - (prob_loss * avg_loss * 100)

        elif stype == "CREDIT_SPREAD":
            # Profit from time decay
            credit = strategy.get("net_credit", max_profit)
            width = strategy.get("spread_width", max_loss + credit)
            avg_win = credit * 0.75 * 100  # Keep 75% of credit
            avg_loss = (width - credit) * 0.70 * 100
            # Credit spreads have higher base probability
            prob_profit = min(0.85, prob_profit + 0.08)
            prob_loss = 1 - prob_profit
            ev = (prob_profit * avg_win) - (prob_loss * avg_loss)

        elif stype == "BROKEN_WING_BUTTERFLY":
            avg_win = max_profit * 0.40 * 100  # BWB rarely hits max
            avg_loss = max_loss * 0.60 * 100
            credit = strategy.get("net_credit", 0)
            if credit > 0:
                # Credit entry = higher probability
                prob_profit = min(0.85, prob_profit + 0.05)
                prob_loss = 1 - prob_profit
            ev = (prob_profit * avg_win) - (prob_loss * avg_loss)

        elif stype == "CALENDAR_SPREAD":
            avg_win = cost * 0.30  # Calendars target 30% return
            avg_loss = cost * 0.50
            ev = (prob_profit * avg_win) - (prob_loss * avg_loss)

        else:
            avg_win = max_profit * 0.50 * 100 if isinstance(max_profit, (int, float)) else cost * 0.50
            avg_loss = max_loss * 0.70 * 100 if isinstance(max_loss, (int, float)) else cost * 0.70
            ev = (prob_profit * avg_win) - (prob_loss * avg_loss)

        ev_ratio = ev / cost if cost > 0 else 0

        # ── KELLY CRITERION ──
        # Optimal bet size = (bp - q) / b
        # b = ratio of win to loss
        # p = probability of win
        # q = probability of loss
        if avg_loss > 0 and avg_win > 0:
            b = avg_win / avg_loss
            kelly = (b * prob_profit - prob_loss) / b
            kelly = max(0, min(0.25, kelly))  # Cap at 25%
        else:
            kelly = 0

        # Probability of reaching max profit
        if stype == "DEBIT_SPREAD":
            prob_max = prob_profit * 0.35
        elif stype == "CREDIT_SPREAD":
            prob_max = prob_profit * 0.55
        elif stype == "NAKED_LONG":
            prob_max = prob_profit * 0.25
        else:
            prob_max = prob_profit * 0.20

        # Grade
        if ev_ratio > 0.20:
            grade = "A"
        elif ev_ratio > 0.10:
            grade = "B"
        elif ev_ratio > 0.05:
            grade = "C"
        elif ev_ratio > 0:
            grade = "D"
        else:
            grade = "F"

        result = {
            "ev_dollar": round(ev, 2),
            "ev_ratio": round(ev_ratio, 4),
            "prob_profit": round(prob_profit, 3),
            "prob_max": round(prob_max, 3),
            "risk_reward": round(avg_win / max(avg_loss, 1), 2),
            "kelly_fraction": round(kelly, 4),
            "grade": grade,
            "is_positive": ev > 0,
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
        }

        return result

    def should_trade(self, ev_result):
        """Check if a trade meets minimum EV threshold."""
        if not ev_result:
            return False, "no_ev_data"
        if not ev_result["is_positive"]:
            return False, f"negative_ev_${ev_result['ev_dollar']:.2f}"
        if ev_result["ev_ratio"] < self.MIN_EV_RATIO:
            return False, f"low_ev_{ev_result['ev_ratio']:.1%}"
        return True, f"EV:${ev_result['ev_dollar']:+.2f} ({ev_result['grade']})"
