"""
OpenClaw helper: end-of-day performance summary.
Called by the trading_eod skill.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORTFOLIO_PATH = Path("C:/Users/User/Desktop/trading_system/config/paper_scalp.json")


def classify_exit(reason):
    if not reason:
        return "unknown"
    r = reason.lower()
    if r.startswith("profit"):
        return "profit_target"
    if r.startswith("stop"):
        return "stop_loss"
    if r.startswith("trail"):
        return "trailing_stop"
    if r.startswith("breakeven"):
        return "breakeven_stop"
    if r.startswith("time"):
        return "time_stop"
    if r.startswith("closing") or r.startswith("eod") or r.startswith("force"):
        return "eod_close"
    return r[:20]


def main():
    if not PORTFOLIO_PATH.exists():
        print("Portfolio file not found.")
        return

    with open(PORTFOLIO_PATH) as f:
        p = json.load(f)

    equity  = p.get("equity", 0)
    history = p.get("history", [])
    today   = datetime.now().strftime("%Y-%m-%d")

    today_trades = [t for t in history if t.get("exit_time", "").startswith(today)]

    print("=" * 55)
    print(f"END OF DAY REPORT  —  {today}")
    print("=" * 55)

    if not today_trades:
        print("No completed trades today.")
        print(f"Current Equity: ${equity:,.2f}")
        return

    pnls   = [t.get("pnl", 0) for t in today_trades]
    wins   = [v for v in pnls if v > 0]
    losses = [v for v in pnls if v < 0]
    flat   = [v for v in pnls if v == 0]
    total  = sum(pnls)
    wr     = len(wins) / len(pnls) * 100 if pnls else 0

    print(f"Trades:     {len(pnls)}")
    print(f"Wins:       {len(wins)}  |  Losses: {len(losses)}  |  Flat: {len(flat)}")
    print(f"Win Rate:   {wr:.0f}%")
    print(f"Total P&L:  ${total:+,.2f}")
    print(f"Avg Trade:  ${total / len(pnls):+,.2f}")
    if wins:
        print(f"Avg Win:    ${sum(wins) / len(wins):+,.2f}")
    if losses:
        print(f"Avg Loss:   ${sum(losses) / len(losses):+,.2f}")

    print()
    if wins:
        best = max(today_trades, key=lambda t: t.get("pnl", 0))
        print(f"Best Trade:   {best.get('direction','?'):4s} {best.get('symbol','?'):6s} "
              f"${best.get('pnl', 0):+,.2f}  [{best.get('exit_reason', '?')}]")
    if losses:
        worst = min(today_trades, key=lambda t: t.get("pnl", 0))
        print(f"Worst Trade:  {worst.get('direction','?'):4s} {worst.get('symbol','?'):6s} "
              f"${worst.get('pnl', 0):+,.2f}  [{worst.get('exit_reason', '?')}]")

    print()
    reasons = Counter(classify_exit(t.get("exit_reason", "")) for t in today_trades)
    print("Exit Breakdown:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        bar = "█" * count
        print(f"  {reason:20s}  {count:2d}  {bar}")

    print()
    start_equity = equity - total
    pct_change   = (total / start_equity * 100) if start_equity > 0 else 0
    print(f"Equity Start:  ${start_equity:,.2f}")
    print(f"Equity End:    ${equity:,.2f}  ({pct_change:+.2f}%)")


if __name__ == "__main__":
    main()
