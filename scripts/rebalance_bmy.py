import os, sys, time, httpx
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
from data.broker.schwab_auth import get_schwab_client
from loguru import logger

client = get_schwab_client()
ah = client.get_account_numbers().json()[0]["hashValue"]

closes = [
    {"symbol": "BMY   260417C00058000", "qty": 8, "desc": "BMY $58C x8"},
    {"symbol": "BMY   260417C00057500", "qty": 2, "desc": "BMY $57.5C x2"},
]

print("REBALANCING BMY...")
for c in closes:
    try:
        from schwab.orders.options import option_sell_to_close_market
        order = option_sell_to_close_market(c["symbol"], c["qty"])
        resp = client.place_order(ah, order)
        if resp.status_code in (201, 200):
            print(f"  SOLD: {c['desc']} - OK")
        else:
            print(f"  FAILED: {c['desc']} - status {resp.status_code}")
            # Try with limit order instead
            try:
                from schwab.orders.options import option_sell_to_close_limit
                # Get current bid
                quote = client.get_quote(c["symbol"])
                bid = quote.json().get(c["symbol"], {}).get("quote", {}).get("bidPrice", 0)
                if bid > 0:
                    order2 = option_sell_to_close_limit(c["symbol"], c["qty"], str(round(bid * 0.95, 2)))
                    resp2 = client.place_order(ah, order2)
                    if resp2.status_code in (201, 200):
                        print(f"  SOLD (limit): {c['desc']} @ ${bid*0.95:.2f}")
                    else:
                        print(f"  LIMIT FAILED: {c['desc']} - status {resp2.status_code}")
            except Exception as e2:
                print(f"  LIMIT ERROR: {e2}")
    except Exception as e:
        print(f"  ERROR: {c['desc']} - {e}")
    time.sleep(1)

print()
print("Rebalance complete. Check ThinkorSwim for confirmations.")
