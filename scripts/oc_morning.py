"""
OpenClaw helper: pre-market morning briefing.
Called by the trading_morning skill.
"""
import json
import sys
from datetime import datetime, date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORTFOLIO_PATH = Path("C:/Users/User/Desktop/trading_system/config/paper_scalp.json")
TOKEN_PATH     = Path("C:/Users/User/Desktop/trading_system/config/schwab_token.json")

FOMC_DATES = [
    "2026-01-29", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]
CPI_DATES = [
    "2026-01-14", "2026-02-12", "2026-03-11", "2026-04-14",
    "2026-05-13", "2026-06-10", "2026-07-15", "2026-08-12",
    "2026-09-16", "2026-10-14", "2026-11-12", "2026-12-09",
]


def token_health():
    if not TOKEN_PATH.exists():
        return None, "NOT FOUND — re-authenticate before trading"
    try:
        with open(TOKEN_PATH) as f:
            token = json.load(f)
        created  = float(token.get("creation_timestamp", 0))
        ct       = datetime.fromtimestamp(created)
        age_days = (datetime.now() - ct).days
        left     = 7 - age_days
        if age_days >= 6:
            return age_days, f"CRITICAL — {age_days}d old, expires in <{left}d. Re-auth NOW."
        elif age_days >= 5:
            return age_days, f"CAUTION  — {age_days}d old, expires in ~{left}d."
        else:
            return age_days, f"OK       — {age_days}d old, ~{left}d remaining."
    except Exception as e:
        return None, f"ERROR reading token: {e}"


def todays_events():
    today = date.today().isoformat()
    d     = date.today()
    events = []
    if today in FOMC_DATES:
        events.append(("FOMC", "Fed rate decision — entries delayed until 9:30 AM CT, expect high vol"))
    if today in CPI_DATES:
        events.append(("CPI",  "Inflation print — entries delayed until 9:30 AM CT"))
    if d.weekday() == 4 and d.day <= 7:
        events.append(("NFP",  "Non-Farm Payrolls (first Friday) — entries delayed until 9:30 AM CT"))
    return events


def main():
    today      = date.today()
    is_weekend = today.weekday() >= 5
    now        = datetime.now()

    print("=" * 55)
    print(f"MORNING BRIEFING  —  {today.strftime('%A, %B %d, %Y')}")
    print("=" * 55)

    if is_weekend:
        print("Market CLOSED  (weekend — no trading today)")
    else:
        print("Market OPEN today")
        print("  Pre-market opens:  7:00 AM CT")
        print("  Regular session:   8:30 AM CT — 3:00 PM CT")
        print("  Scalper window:    8:35 AM CT — 2:45 PM CT")

    print()

    # Economic events
    events = todays_events()
    if events:
        print("ECONOMIC EVENTS TODAY:")
        for tag, note in events:
            print(f"  [{tag}]  {note}")
    else:
        print("Economic Events:  None scheduled")

    print()

    # Portfolio
    if PORTFOLIO_PATH.exists():
        with open(PORTFOLIO_PATH) as f:
            p = json.load(f)

        equity   = p.get("equity", 0)
        settled  = p.get("settled_cash", 0)
        history  = p.get("history", [])

        # Prior day P&L
        if today.day > 1:
            yesterday = today.replace(day=today.day - 1).isoformat()
        else:
            yesterday = ""
        prior_trades = [t for t in history if t.get("exit_time", "").startswith(yesterday)] if yesterday else []
        prior_pnl    = sum(t.get("pnl", 0) for t in prior_trades)

        trade_size = equity * 0.02

        print("PORTFOLIO:")
        print(f"  Equity:         ${equity:>10,.2f}")
        print(f"  Settled Cash:   ${settled:>10,.2f}  <- funds available today")
        print(f"  2% Trade Size:  ${trade_size:>10,.0f}  per position (at full confidence)")
        if prior_trades:
            print(f"  Prior Day P&L:  ${prior_pnl:>+10,.2f}  ({len(prior_trades)} trades)")
    else:
        print("PORTFOLIO:  file not found")

    print()

    # Token
    age, status = token_health()
    print(f"SCHWAB TOKEN:  {status}")

    print()

    # Readiness
    print("SYSTEM READINESS:")
    issues = []
    if is_weekend:
        issues.append("Market is closed (weekend)")
    if age is not None and age >= 6:
        issues.append("Schwab token expires very soon — re-authenticate first")
    if events:
        issues.append(f"Event day ({', '.join(t for t,_ in events)}) — elevated volatility expected")

    if issues:
        for issue in issues:
            print(f"  ! {issue}")
    else:
        print("  All clear.")
        print()
        print("  To start the scalper:")
        print("    cd C:\\Users\\User\\Desktop\\trading_system")
        print("    python scripts/scalper_live.py")


if __name__ == "__main__":
    main()
