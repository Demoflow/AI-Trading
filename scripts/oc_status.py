"""
OpenClaw helper: current scalper portfolio status.
Called by the trading_status skill.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORTFOLIO_PATH = Path("C:/Users/User/Desktop/trading_system/config/paper_scalp.json")


def main():
    if not PORTFOLIO_PATH.exists():
        print("Portfolio file not found.")
        return

    with open(PORTFOLIO_PATH) as f:
        p = json.load(f)

    equity   = p.get("equity", 0)
    cash     = p.get("cash", 0)
    settled  = p.get("settled_cash", 0)
    positions = p.get("positions", [])
    history  = p.get("history", [])

    today = datetime.now().strftime("%Y-%m-%d")
    today_trades = [t for t in history if t.get("exit_time", "").startswith(today)]
    today_pnl    = sum(t.get("pnl", 0) for t in today_trades)
    today_wins   = sum(1 for t in today_trades if t.get("pnl", 0) > 0)
    today_losses = sum(1 for t in today_trades if t.get("pnl", 0) <= 0)

    print("=" * 55)
    print(f"SCALPER STATUS  —  {datetime.now().strftime('%Y-%m-%d  %H:%M CT')}")
    print("=" * 55)
    print(f"Equity:        ${equity:>10,.2f}")
    print(f"Settled Cash:  ${settled:>10,.2f}  (available to trade today)")
    print(f"Cash:          ${cash:>10,.2f}")
    print()

    if positions:
        print(f"OPEN POSITIONS  ({len(positions)}):")
        for pos in positions:
            symbol    = pos.get("symbol", "?")
            direction = pos.get("direction", "?")
            contract  = pos.get("contract", "")
            qty       = pos.get("qty", 0)
            entry_cost = pos.get("entry_cost", 0)
            entry_time = (pos.get("entry_time") or "")[:16]
            print(f"  {direction:4s}  {symbol:6s}  {contract}  x{qty}"
                  f"  cost=${entry_cost:,.0f}  entered={entry_time}")
    else:
        print("OPEN POSITIONS:  None")

    print()
    print(f"TODAY  ({today}):")
    count = len(today_trades)
    print(f"  Trades: {count}  |  Wins: {today_wins}  |  Losses: {today_losses}")
    if count:
        print(f"  Win Rate: {today_wins / count * 100:.0f}%")
    print(f"  P&L: ${today_pnl:+,.2f}")

    max_loss  = equity * 0.08
    remaining = max_loss + today_pnl
    print()
    print(f"RISK BUFFER:")
    print(f"  Daily loss limit: ${max_loss:,.0f}")
    print(f"  Remaining before halt: ${max(remaining, 0):,.0f}")
    if remaining <= 0:
        print("  *** DAILY LOSS LIMIT REACHED — SCALPER HALTED ***")


if __name__ == "__main__":
    main()
