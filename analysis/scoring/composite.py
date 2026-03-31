"""
Composite Scoring Engine.
Redistributes options flow weight when no Schwab.
"""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
    )
)

from loguru import logger
from analysis.scoring.technical_score import TechnicalScorer
from analysis.scoring.options_flow_score import OptionsFlowScorer
from analysis.scoring.fundamental_score import FundamentalScorer
from analysis.scoring.market_context_score import MarketContextScorer
from analysis.scoring.risk_reward_score import RiskRewardScorer


class CompositeScorer:

    WEIGHTS_FULL = {
        "technical": 0.30,
        "options_flow": 0.25,
        "fundamental": 0.15,
        "market_context": 0.15,
        "risk_reward": 0.15,
    }

    WEIGHTS_NO_FLOW = {
        "technical": 0.40,
        "options_flow": 0.00,
        "fundamental": 0.20,
        "market_context": 0.20,
        "risk_reward": 0.20,
    }

    ENTRY_THRESHOLD = 70
    REDUCED_THRESHOLD = 60

    def __init__(self):
        self.technical = TechnicalScorer()
        self.options_flow = OptionsFlowScorer()
        self.fundamental = FundamentalScorer()
        self.market_context = MarketContextScorer()
        self.risk_reward = RiskRewardScorer()

    def score_stock(
        self, symbol, stock_df, spy_df,
        sector_df=None, vix_price=None,
        chain_data=None,
        stock_sector="Technology"
    ):
        results = {}
        has_flow = chain_data is not None

        weights = (
            self.WEIGHTS_FULL if has_flow
            else self.WEIGHTS_NO_FLOW
        )

        tech = self.technical.score(stock_df)
        results["technical"] = tech["total_score"]

        if has_flow:
            flow = self.options_flow.score(
                chain_data, direction="CALL"
            )
        else:
            flow = {
                "total_score": 0,
                "details": {"note": "no chain data"}
            }
        results["options_flow"] = flow["total_score"]

        fund = self.fundamental.score(symbol)
        results["fundamental"] = fund["total_score"]
        earnings_blocked = fund.get(
            "earnings_blocked", False
        )

        market = self.market_context.score(
            symbol, stock_sector, spy_df,
            sector_df, vix_price, stock_df
        )
        results["market_context"] = market["total_score"]

        rr = self.risk_reward.score(stock_df)
        results["risk_reward"] = rr["total_score"]

        composite = sum(
            results[k] * weights[k]
            for k in weights
        )

        override_reason = None
        if earnings_blocked:
            composite = min(composite, 40)
            override_reason = "Earnings within 5 days"
        elif vix_price and vix_price > 30:
            composite *= 0.80
            override_reason = "VIX > 30 panic regime"

        if composite >= self.ENTRY_THRESHOLD:
            action = "ENTER"
            size_modifier = 1.0
        elif composite >= self.REDUCED_THRESHOLD:
            action = "ENTER_REDUCED"
            size_modifier = 0.5
        else:
            action = "SKIP"
            size_modifier = 0

        latest = stock_df.iloc[-1]
        atr = latest.get(
            "atr_14", latest["close"] * 0.02
        )
        entry_price = latest["close"]

        # Use real support for stop (#7)
        levels = rr.get("levels", {})
        rec_stop = levels.get(
            "recommended_stop",
            round(entry_price - (2 * atr), 2)
        )
        stop_dist = entry_price - rec_stop

        # Targets based on stop distance (#5)
        target_1 = round(
            entry_price + (1.5 * stop_dist), 2
        )
        target_2 = round(
            entry_price + (2.5 * stop_dist), 2
        )
        target_3 = round(
            entry_price + (4.0 * stop_dist), 2
        )

        return {
            "symbol": symbol,
            "composite_score": round(composite, 1),
            "action": action,
            "size_modifier": size_modifier,
            "sub_scores": {
                k: round(v, 1)
                for k, v in results.items()
            },
            "trade_params": {
                "entry_price": round(entry_price, 2),
                "stop_loss": rec_stop,
                "target_1": target_1,
                "target_2": target_2,
                "target_3": target_3,
                "atr": round(atr, 2),
                "stop_type": levels.get(
                    "support_type", "atr_2x"
                ),
            },
            "key_levels": levels,
            "override": override_reason,
            "details": {
                "technical": tech.get("details", {}),
                "options_flow": flow.get("details", {}),
                "fundamental": fund.get("details", {}),
                "market_context": market.get(
                    "details", {}
                ),
                "risk_reward": rr.get("details", {}),
            },
        }

    def score_for_puts(
        self, symbol, stock_df, spy_df,
        sector_df=None, vix_price=None,
        chain_data=None,
        stock_sector="Technology"
    ):
        result = self.score_stock(
            symbol, stock_df, spy_df,
            sector_df, vix_price,
            chain_data, stock_sector
        )
        tech_bear = self.technical.score_for_puts(
            stock_df
        )
        bear_tech = tech_bear["total_score"]
        result["sub_scores"]["technical"] = bear_tech

        has_flow = chain_data is not None
        weights = (
            self.WEIGHTS_FULL if has_flow
            else self.WEIGHTS_NO_FLOW
        )

        if has_flow:
            flow_bear = self.options_flow.score(
                chain_data, direction="PUT"
            )
            bear_flow = flow_bear["total_score"]
            result["sub_scores"]["options_flow"] = bear_flow

        new_comp = sum(
            result["sub_scores"][k] * weights[k]
            for k in weights
        )
        result["composite_score"] = round(new_comp, 1)

        latest = stock_df.iloc[-1]
        atr = latest.get(
            "atr_14", latest["close"] * 0.02
        )
        sl = round(latest["close"] + (2 * atr), 2)
        t1 = round(latest["close"] - (1.5 * atr), 2)  # ATR-based
        t2 = round(latest["close"] - (2.5 * atr), 2)  # ATR-based
        result["trade_params"]["stop_loss"] = sl
        result["trade_params"]["target_1"] = t1
        result["trade_params"]["target_2"] = t2
        result["direction"] = "BEARISH"
        return result
