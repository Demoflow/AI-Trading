"""
Sync Equity - Updates .env ACCOUNT_EQUITY based on
current paper portfolio value.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def sync():
    pp = "config/paper_options.json"
    if not os.path.exists(pp):
        print("No portfolio file found.")
        return

    with open(pp) as f:
        data = json.load(f)

    cash = data.get("cash", 0)
    positions = data.get("positions", [])
    open_pos = [p for p in positions if p.get("status") == "OPEN"]
    deployed = sum(p.get("entry_cost", 0) for p in open_pos)
    total = cash + deployed

    print(f"Cash:     ${cash:,.2f}")
    print(f"Deployed: ${deployed:,.2f}")
    print(f"Total:    ${total:,.2f}")

    # Update .env
    env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

        new_lines = []
        found = False
        for line in lines:
            if line.startswith("ACCOUNT_EQUITY="):
                new_lines.append(f"ACCOUNT_EQUITY={total:.2f}\n")
                found = True
            else:
                new_lines.append(line)

        if not found:
            new_lines.append(f"ACCOUNT_EQUITY={total:.2f}\n")

        with open(env_path, "w") as f:
            f.writelines(new_lines)

        print(f"Updated .env: ACCOUNT_EQUITY={total:.2f}")
    else:
        print(".env not found")


if __name__ == "__main__":
    sync()
