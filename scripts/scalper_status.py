"""Scalper Status — Long Options portfolio."""

import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def status():
    path = "config/paper_scalp.json"
    if not os.path.exists(path):
        print("No scalper portfolio. Run: python scripts/scalper_live.py")
        return
    with open(path) as f:
        data = json.load(f)

    equity = data.get("equity", 25000)
    cash = data.get("cash", equity)
    settled = data.get("settled_cash", cash)
    history = data.get("history", [])
    daily = data.get("daily_stats", {})

    total_pnl = sum(t.get("pnl", 0) for t in history)
    wins = [t for t in history if t.get("pnl", 0) > 0]
    losses = [t for t in history if t.get("pnl", 0) <= 0]
    wr = len(wins) / max(len(history), 1)

    print()
    print("=" * 60)
    print("  0DTE SCALPER STATUS — LONG OPTIONS")
    print("=" * 60)
    print(f"  Equity:        ${equity:>10,.2f}")
    print(f"  Cash:          ${cash:>10,.2f}")
    print(f"  Settled Cash:  ${settled:>10,.2f}  (available today)")
    print(f"  Total P&L:     ${total_pnl:>+10,.2f}")
    print(f"  Return:        {total_pnl/25000:>+10.1%}  (vs $25,000 start)")
    print(f"  Trades: {len(history)} | W:{len(wins)} L:{len(losses)} WR:{wr:.0%}")
    if wins:
        print(f"  Avg Win:  ${sum(t['pnl'] for t in wins)/len(wins):+,.2f}")
    if losses:
        print(f"  Avg Loss: ${sum(t['pnl'] for t in losses)/len(losses):+,.2f}")

    positions = [p for p in data.get("positions", []) if p.get("status") == "OPEN"]
    if positions:
        print(f"\n  OPEN ({len(positions)}):")
        for p in positions:
            print(
                f"    {p['direction']} {p['symbol']} ${p['strike']} "
                f"d={p.get('delta', '?')} "
                f"cost=${p['entry_cost']:,.2f} "
                f"conf:{p.get('confidence', '?')}"
            )

    if daily:
        print(f"\n  DAILY:")
        for d in sorted(daily.keys())[-5:]:
            s = daily[d]
            print(f"    {d}: {s['trades']}t W:{s['wins']} L:{s['losses']} ${s['pnl']:+,.2f}")

    if history:
        print(f"\n  RECENT:")
        for t in history[-10:]:
            r = "W" if t.get("pnl", 0) > 0 else "L"
            mins = ""
            if t.get("entry_time") and t.get("exit_time"):
                try:
                    et = datetime.fromisoformat(t["entry_time"])
                    xt = datetime.fromisoformat(t["exit_time"])
                    mins = f" {(xt-et).total_seconds()/60:.0f}m"
                except Exception:
                    pass
            print(
                f"    [{r}] {t.get('direction','?')} {t['symbol']} "
                f"{t.get('signal_type','?')} "
                f"${t.get('pnl', 0):+,.2f}{mins}"
            )
    print("=" * 60)


if __name__ == "__main__":
    status()
