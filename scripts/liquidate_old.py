"""
Liquidate Old Positions.
Closes all positions opened before the Elite v5 upgrade.
Frees capital for reallocation into optimized strategies.
"""

import os
import sys
import json
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def get_live_value(client, sym):
    if not client or not sym:
        return None
    try:
        import httpx
        resp = client.get_quote(sym)
        if resp.status_code == httpx.codes.OK:
            data = resp.json()
            q = data.get(sym, {}).get("quote", {})
            mark = q.get("mark", 0)
            bid = q.get("bidPrice", 0)
            ask = q.get("askPrice", 0)
            if mark > 0:
                return mark
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            if bid > 0:
                return bid
    except Exception:
        pass
    return None


def get_position_value(client, pos):
    legs = pos.get("legs", [])
    if legs:
        total = 0
        for leg in legs:
            sym = leg.get("symbol", "")
            val = get_live_value(client, sym)
            if val is None:
                return None
            if leg["leg"] == "LONG":
                total += val
            else:
                total -= val
        return round(total, 2)
    csym = pos.get("contract", "")
    if csym:
        return get_live_value(client, csym)
    return None


def liquidate():
    from utils.logging_setup import setup_logging
    setup_logging()

    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.error(f"Schwab required: {e}")
        return

    pp = "config/paper_options.json"
    if not os.path.exists(pp):
        logger.info("No portfolio found.")
        return

    with open(pp) as f:
        data = json.load(f)

    positions = data.get("positions", [])
    open_pos = [p for p in positions if p.get("status") == "OPEN"]

    if not open_pos:
        logger.info("No open positions to liquidate.")
        return

    SLIPPAGE = 0.025

    logger.info("=" * 60)
    logger.info("LIQUIDATING OLD POSITIONS")
    logger.info(f"Open positions: {len(open_pos)}")
    logger.info("=" * 60)

    total_pnl = 0
    total_proceeds = 0

    for pos in open_pos:
        sym = pos.get("underlying", "?")
        direction = pos.get("direction", "?")
        entry_cost = pos.get("entry_cost", 0)
        stype = pos.get("strategy_type", "NAKED_LONG")

        current = get_position_value(client, pos)

        if current is None or current <= 0:
            logger.warning(f"  {sym}: No live quote - using entry value")
            current = pos.get("entry_price", entry_cost / 100)

        # Apply slippage (selling)
        sell_price = round(current * (1 - SLIPPAGE), 2)

        if stype == "CREDIT_SPREAD":
            credit = pos.get("credit_received", 0)
            qty = pos["legs"][0]["qty"] if pos.get("legs") else 1
            close_cost = abs(sell_price) * qty * 100
            pnl = credit - close_cost
            data["cash"] += pos["entry_cost"]  # return collateral
            data["cash"] += pnl
            proceeds = pos["entry_cost"] + pnl
        else:
            # Old format: single contract
            if pos.get("legs"):
                qty = pos["legs"][0]["qty"]
            else:
                qty = pos.get("qty", 1)
            proceeds = sell_price * qty * 100
            pnl = proceeds - entry_cost
            data["cash"] += proceeds

        pos["status"] = "CLOSED"
        pos["exit_date"] = date.today().isoformat()
        pos["exit_value"] = sell_price
        pos["pnl"] = round(pnl, 2)
        pos["pnl_pct"] = round(pnl / max(entry_cost, 1) * 100, 1)
        pos["exit_reason"] = "LIQUIDATION_UPGRADE"

        result = "WIN" if pnl > 0 else "LOSS"
        total_pnl += pnl
        total_proceeds += proceeds

        logger.info(
            f"  [{result}] {direction:4s} {sym:6s} "
            f"{stype:14s} "
            f"Cost:${entry_cost:>7,.2f} "
            f"Exit:${proceeds:>7,.2f} "
            f"P&L:${pnl:>+8,.2f} ({pos['pnl_pct']:+.1f}%)"
        )

    # Save
    with open(pp, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info("=" * 60)
    logger.info("LIQUIDATION COMPLETE")
    logger.info(f"  Closed: {len(open_pos)} positions")
    logger.info(f"  Total P&L: ${total_pnl:+,.2f}")
    logger.info(f"  Cash freed: ${total_proceeds:,.2f}")
    logger.info(f"  Cash available: ${data['cash']:,.2f}")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Run evening scan tonight: EVENING_SCAN.bat")
    logger.info("  2. System will find new trades with Elite v5.2 engine")
    logger.info("  3. Tomorrow morning: MORNING_TRADE.bat (auto-scheduled)")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    print()
    print("=" * 60)
    print("  LIQUIDATE ALL OPEN POSITIONS")
    print("  This will close all current positions at market value")
    print("  to free cash for the upgraded Elite v5.2 system.")
    print("=" * 60)
    print()

    confirm = input("Type LIQUIDATE to confirm: ")
    if confirm != "LIQUIDATE":
        print("Cancelled.")
        exit()

    liquidate()
