"""Reset scalper portfolio for fresh start."""
import os
import json

path = "config/paper_scalp.json"
data = {
    "equity": 25000,
    "cash": 25000,
    "positions": [],
    "history": [],
    "daily_stats": {},
}
os.makedirs("config", exist_ok=True)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
print("Scalper portfolio reset to $25,000")
