"""
OpenClaw helper: scalper process status check.
Called by the scalper_control skill.
"""
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORTFOLIO_PATH = Path("C:/Users/User/Desktop/trading_system/config/paper_scalp.json")
TOKEN_PATH     = Path("C:/Users/User/Desktop/trading_system/config/schwab_token.json")


def is_scalper_running():
    """Returns True if scalper_live.py is running as a Python process."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "commandline", "/FORMAT:CSV"],
            capture_output=True, text=True, timeout=5
        )
        return "scalper_live" in result.stdout
    except Exception:
        # Fallback: check tasklist for python processes
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe"],
                capture_output=True, text=True, timeout=5
            )
            # Can't confirm scalper specifically without wmic, return unknown
            has_python = "python.exe" in result.stdout
            return None if not has_python else "unknown"
        except Exception:
            return None


def token_age():
    if not TOKEN_PATH.exists():
        return None
    try:
        with open(TOKEN_PATH) as f:
            token = json.load(f)
        created = float(token.get("creation_timestamp", 0))
        ct = datetime.fromtimestamp(created)
        return (datetime.now() - ct).days
    except Exception:
        return None


def main():
    now = datetime.now()
    h   = now.hour + now.minute / 60.0
    is_market = 8.4 <= h < 15.1 and now.weekday() < 5

    print("=" * 55)
    print(f"SCALPER CONTROL  —  {now.strftime('%H:%M')} CT")
    print("=" * 55)

    # Process check
    running = is_scalper_running()
    if running is True:
        print("Scalper Process:  RUNNING  ✓")
    elif running is False:
        print("Scalper Process:  NOT RUNNING")
    elif running == "unknown":
        print("Scalper Process:  UNKNOWN (Python is running but couldn't confirm scalper_live)")
    else:
        print("Scalper Process:  COULD NOT CHECK (wmic unavailable)")

    # Market hours
    print()
    status = "OPEN" if is_market else "CLOSED"
    print(f"Market:   {status}  ({now.strftime('%H:%M')} CT)")
    print(f"Window:   8:35 AM – 2:45 PM CT  (Mon–Fri)")

    # Portfolio
    print()
    if PORTFOLIO_PATH.exists():
        with open(PORTFOLIO_PATH) as f:
            p = json.load(f)
        equity    = p.get("equity", 0)
        settled   = p.get("settled_cash", 0)
        positions = p.get("positions", [])
        print(f"Equity:         ${equity:,.2f}")
        print(f"Settled Cash:   ${settled:,.2f}  (available today)")
        print(f"Open Positions: {len(positions)}")
    else:
        print("Portfolio file not found.")

    # Token
    age = token_age()
    print()
    if age is None:
        print("Token: NOT FOUND — must re-authenticate before trading")
    elif age >= 6:
        print(f"Token: WARNING — {age} days old, expires very soon. Re-auth required.")
    else:
        print(f"Token: OK — {age} days old (~{7 - age} days remaining)")

    # Start instructions
    print()
    if running is False:
        print("To start the scalper, open a terminal and run:")
        print("  cd C:\\Users\\User\\Desktop\\trading_system")
        print("  python scripts/scalper_live.py")


if __name__ == "__main__":
    main()
