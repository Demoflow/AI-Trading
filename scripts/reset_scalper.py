"""Reset scalper portfolio to fresh $25,000 start."""
import os
import json
from datetime import date

path = "config/paper_scalp.json"
data = {
    "equity": 25000,
    "cash": 25000,
    "settled_cash": 25000,
    "settlement_date": date.today().isoformat(),
    "positions": [],
    "history": [],
    "daily_stats": {},
}
os.makedirs("config", exist_ok=True)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("Scalper portfolio reset to $25,000")
