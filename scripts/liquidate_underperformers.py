"""
Liquidate CVX 207.5P and PFE 27.5C at market.
Run after 8:30 AM CT.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from data.broker.schwab_auth import get_schwab_client
from loguru import logger

client = get_schwab_client()
ah = client.get_account_numbers().json()[1]["hashValue"]  # Brokerage account

closes = [
    {"symbol": "CVX   260417P00207500", "qty": 2, "desc": "CVX $207.5P x2 (-31.4%)"},
    {"symbol": "PFE   260417C00027500", "qty": 3, "desc": "PFE $27.5C x3 (-22.5%)"},
]

print("=" * 60)
print("  LIQUIDATE UNDERPERFORMERS")
print("=" * 60)
print()

for c in closes:
    print(f"  Selling: {c['desc']}")

print()

for c in closes:
    try:
        from schwab.orders.options import option_sell_to_close_market
        order = option_sell_to_close_market(c["symbol"], c["qty"])
        resp = client.place_order(ah, order)
        if resp.status_code in (200, 201):
            print(f"  SOLD: {c['desc']} - OK")
        else:
            print(f"  FAILED: {c['desc']} - status {resp.status_code}")
            # Try limit at bid
            try:
                from schwab.orders.options import option_sell_to_close_limit
                q = client.get_quote(c["symbol"])
                bid = q.json().get(c["symbol"], {}).get("quote", {}).get("bidPrice", 0)
                if bid > 0:
                    order2 = option_sell_to_close_limit(c["symbol"], c["qty"], str(round(bid * 0.95, 2)))
                    resp2 = client.place_order(ah, order2)
                    if resp2.status_code in (200, 201):
                        print(f"  SOLD (limit @ ${bid*0.95:.2f}): {c['desc']}")
                    else:
                        print(f"  LIMIT FAILED: {c['desc']} - status {resp2.status_code}")
            except Exception as e2:
                print(f"  LIMIT ERROR: {e2}")
    except Exception as e:
        print(f"  ERROR: {c['desc']} - {e}")
    time.sleep(1)

print()
print("Liquidation complete. Check ThinkorSwim for confirmations.")
print("Freed capital will be available for next scan.")
