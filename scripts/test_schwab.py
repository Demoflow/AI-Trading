import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from data.broker.schwab_auth import get_schwab_client, get_account_hash, get_account_positions

c = get_schwab_client()
h = get_account_hash(c)
p = get_account_positions(c, h)

print(f"Cash: ${p['cash_available']:,.2f}")
print(f"Equity: ${p['equity']:,.2f}")
print(f"Positions: {len(p['positions'])}")
for pos in p['positions']:
    print(f"  {pos['symbol']}: {pos['quantity']} shares @ ${pos['avg_price']:.2f}")