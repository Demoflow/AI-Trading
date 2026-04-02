"""Close all spread positions for cash account conversion."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from loguru import logger
from data.broker.schwab_auth import get_schwab_client
from aggressive.options_executor import OptionsExecutor

def run():
    logger.info("=" * 60)
    logger.info("CLOSING ALL SPREADS FOR CASH ACCOUNT CONVERSION")
    logger.info("=" * 60)

    client = get_schwab_client()
    executor = OptionsExecutor(client, "t", paper_mode=False)
    positions = executor.get_live_positions()

    logger.info(f"Found {len(positions)} legs")
    for p in positions:
        logger.info(f"  {p['underlying']:5} {p['symbol'][:25]} qty={p['qty']:+3} mkt=${p['market_value']:.2f}")

    # Group by underlying
    from collections import defaultdict
    by_sym = defaultdict(list)
    for p in positions:
        by_sym[p["underlying"]].append(p)

    for sym, legs in by_sym.items():
        long_legs = [l for l in legs if l["qty"] > 0]
        short_legs = [l for l in legs if l["qty"] < 0]

        if long_legs and short_legs:
            # This is a spread — close as a unit
            logger.info(f"\nCLOSING SPREAD: {sym}")
            pos_for_close = {
                "underlying": sym,
                "strategy_type": "DEBIT_SPREAD",
                "legs": [],
            }
            for l in long_legs:
                pos_for_close["legs"].append({"symbol": l["symbol"], "leg": "LONG", "qty": abs(l["qty"])})
            for s in short_legs:
                pos_for_close["legs"].append({"symbol": s["symbol"], "leg": "SHORT", "qty": abs(s["qty"])})
            result = executor.close_position_live(pos_for_close)
            logger.info(f"  Result: {result}")
            time.sleep(2)

        elif short_legs and not long_legs:
            # Orphaned short leg — buy to close
            for sl in short_legs:
                logger.info(f"\nBUY TO CLOSE: {sym} {sl['symbol']} qty={abs(sl['qty'])}")
                try:
                    from schwab.orders.options import option_buy_to_close_market
                    order = option_buy_to_close_market(sl["symbol"], abs(sl["qty"]))
                    ah = None
                    accts = client.get_account_numbers().json()
                    for a in accts:
                        if a["accountNumber"] == "28135437":
                            ah = a["hashValue"]
                    if ah:
                        resp = client.place_order(ah, order)
                        logger.info(f"  Status: {resp.status_code}")
                    else:
                        logger.error("  Could not find account hash")
                except Exception as e:
                    logger.error(f"  Error: {e}")
                time.sleep(2)

        elif long_legs and not short_legs:
            # Single long legs — sell to close
            for ll in long_legs:
                logger.info(f"\nSELL TO CLOSE: {sym} {ll['symbol']} qty={ll['qty']}")
                try:
                    from schwab.orders.options import option_sell_to_close_market
                    order = option_sell_to_close_market(ll["symbol"], ll["qty"])
                    ah = None
                    accts = client.get_account_numbers().json()
                    for a in accts:
                        if a["accountNumber"] == "28135437":
                            ah = a["hashValue"]
                    if ah:
                        resp = client.place_order(ah, order)
                        logger.info(f"  Status: {resp.status_code}")
                    else:
                        logger.error("  Could not find account hash")
                except Exception as e:
                    logger.error(f"  Error: {e}")
                time.sleep(2)

    # Verify
    time.sleep(5)
    remaining = executor.get_live_positions()
    logger.info(f"\nRemaining positions: {len(remaining)}")
    if remaining:
        for p in remaining:
            logger.info(f"  {p['underlying']:5} qty={p['qty']:+3}")
    else:
        logger.info("ALL POSITIONS CLOSED - Ready to call Schwab for cash conversion")

    summary = executor.get_live_summary()
    logger.info(f"Cash: ${summary['cash']:,.2f}")

if __name__ == "__main__":
    run()
