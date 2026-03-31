"""
Position Scaler - Add to Winners.
If a position is profitable and thesis intact,
add more contracts to ride the momentum.
"""

import os
import json
from datetime import date
from loguru import logger


class PositionScaler:

    # Only add if position is up at least 20%
    MIN_PROFIT_TO_ADD = 0.20
    # Max times to add to a position
    MAX_ADDS = 2
    # Days after entry before considering adding
    MIN_DAYS_BEFORE_ADD = 2
    # Don't add if DTE < 10
    MIN_DTE_TO_ADD = 10

    def __init__(self, executor, equity):
        self.executor = executor
        self.equity = equity

    def check_add_opportunities(self, client, positions):
        """Check if any open positions deserve adding."""
        adds = []

        for pos in positions:
            if pos.get("status") != "OPEN":
                continue

            adds_done = pos.get("adds", 0)
            if adds_done >= self.MAX_ADDS:
                continue

            entry_date = pos.get("entry_date", "")
            if entry_date:
                days = (date.today() - date.fromisoformat(entry_date)).days
                if days < self.MIN_DAYS_BEFORE_ADD:
                    continue

            dte = pos.get("dte", 30) - (date.today() - date.fromisoformat(entry_date)).days if entry_date else 30
            if dte < self.MIN_DTE_TO_ADD:
                continue

            # Get current option value
            csym = pos.get("contract", "")
            if not csym:
                continue

            try:
                import httpx
                resp = client.get_quote(csym)
                if resp.status_code != httpx.codes.OK:
                    continue
                data = resp.json()
                q = data.get(csym, {}).get("quote", {})
                current = q.get("mark", 0)
                if current <= 0:
                    bid = q.get("bidPrice", 0)
                    ask = q.get("askPrice", 0)
                    current = (bid + ask) / 2
            except Exception:
                continue

            entry = pos.get("entry_price", 0)
            if entry <= 0 or current <= 0:
                continue

            pnl_pct = (current - entry) / entry

            if pnl_pct >= self.MIN_PROFIT_TO_ADD:
                # Calculate add size (half of original)
                add_qty = max(1, pos.get("qty", 1) // 2)
                add_cost = add_qty * current * 100

                # Check if we have cash
                if self.executor.paper_mode:
                    cash = self.executor.paper_positions.get("cash", 0)
                else:
                    cash = self.equity * 0.3

                if add_cost > cash * 0.15:
                    continue

                adds.append({
                    "symbol": pos.get("underlying", ""),
                    "contract": csym,
                    "direction": pos.get("direction", ""),
                    "current_price": round(current, 2),
                    "add_qty": add_qty,
                    "add_cost": round(add_cost, 2),
                    "profit_pct": round(pnl_pct * 100, 1),
                    "position": pos,
                })

                logger.info(
                    f"ADD OPPORTUNITY: {pos.get('underlying', '')} "
                    f"+{pnl_pct:.0%} | Add {add_qty} @ ${current:.2f} "
                    f"= ${add_cost:,.2f}"
                )

        return adds

    def execute_add(self, add_info):
        """Execute the add-to-winner order."""
        result = self.executor.buy_option(
            contract_symbol=add_info["contract"],
            qty=add_info["add_qty"],
            limit_price=add_info["current_price"],
            direction=add_info["direction"],
            underlying=add_info["symbol"],
            strike=add_info["position"].get("strike", 0),
            dte=add_info["position"].get("dte", 30),
        )

        if result.get("status") in ("SUBMITTED", "FILLED"):
            add_info["position"]["adds"] = add_info["position"].get("adds", 0) + 1
            add_info["position"]["qty"] += add_info["add_qty"]
            add_info["position"]["entry_cost"] += add_info["add_cost"]
            # Recalculate avg entry
            total_qty = add_info["position"]["qty"]
            add_info["position"]["entry_price"] = round(
                add_info["position"]["entry_cost"] / (total_qty * 100), 2
            )
            logger.info(
                f"ADDED: {add_info['symbol']} "
                f"+{add_info['add_qty']} contracts "
                f"(total: {total_qty})"
            )
            return True
        return False
