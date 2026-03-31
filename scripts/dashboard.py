"""
Combined Trading Dashboard.
Shows real-time P&L for BOTH systems:
- Elite v6 (swing options)
- 0DTE Scalper
Auto-refreshes every 30 seconds.
"""

import os
import sys
import json
import time
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def get_live_value(client, sym):
    if not client or not sym:
        return None
    try:
        import httpx
        resp = client.get_quote(sym)
        if resp.status_code == httpx.codes.OK:
            data = resp.json()
            q = data.get(sym, {}).get("quote", {})
            mark = q.get("mark", 0)
            bid = q.get("bidPrice", 0)
            ask = q.get("askPrice", 0)
            if mark > 0:
                return mark
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            if bid > 0:
                return bid
    except Exception:
        pass
    return None


def get_position_value(client, pos):
    legs = pos.get("legs", [])
    if legs:
        total = 0
        for leg in legs:
            sym = leg.get("symbol", "")
            val = get_live_value(client, sym)
            if val is None:
                return None
            if leg["leg"] == "LONG":
                total += val
            else:
                total -= val
        return round(total, 2)
    csym = pos.get("contract", "")
    if csym:
        return get_live_value(client, csym)
    return None


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def dashboard(auto_refresh=True):
    client = None
    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
    except Exception:
        pass

    while True:
        clear_screen()
        now = datetime.now()

        print()
        print("=" * 72)
        print(f"  TRADING COMMAND CENTER | {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print("  " + ("Live Schwab quotes" if client else "OFFLINE"))
        print("=" * 72)

        # ── VIX ──
        vix = 0
        if client:
            try:
                import httpx
                r = client.get_quote("$VIX")
                if r.status_code == httpx.codes.OK:
                    vix = r.json().get("$VIX", {}).get("quote", {}).get("lastPrice", 0)
            except Exception:
                pass

        spy_price = 0
        qqq_price = 0
        if client:
            try:
                r = client.get_quote("SPY")
                if r.status_code == 200:
                    spy_price = r.json().get("SPY", {}).get("quote", {}).get("lastPrice", 0)
                r = client.get_quote("QQQ")
                if r.status_code == 200:
                    qqq_price = r.json().get("QQQ", {}).get("quote", {}).get("lastPrice", 0)
            except Exception:
                pass

        print(f"  SPY: ${spy_price:>8.2f}  |  QQQ: ${qqq_price:>8.2f}  |  VIX: {vix:.1f}")
        print()

        # ══════════════════════════════════════
        # ELITE v6 SECTION
        # ══════════════════════════════════════
        elite_eq = float(os.environ.get("ACCOUNT_EQUITY", "7872"))
        elite_cash = 0
        elite_positions = []
        elite_unrealized = 0
        elite_realized = 0

        ep = "config/paper_options.json"
        if os.path.exists(ep):
            with open(ep) as f:
                edata = json.load(f)
            elite_cash = edata.get("cash", 0)
            all_pos = edata.get("positions", [])
            elite_positions = [p for p in all_pos if p.get("status") == "OPEN"]
            closed = [p for p in all_pos if p.get("status") == "CLOSED"]
            elite_realized = sum(p.get("pnl", 0) for p in closed)

            for p in elite_positions:
                current = get_position_value(client, p)
                cost = p.get("entry_cost", 0)
                if current is not None:
                    stype = p.get("strategy_type", "NAKED_LONG")
                    if p.get("legs"):
                        qty = p["legs"][0]["qty"]
                    else:
                        qty = p.get("qty", 1)
                    cur_val = current * qty * 100
                    pnl = cur_val - cost
                else:
                    pnl = 0
                elite_unrealized += pnl

        elite_total = elite_realized + elite_unrealized
        elite_value = elite_cash + sum(
            p.get("entry_cost", 0) for p in elite_positions
        ) + elite_unrealized

        print("  ELITE v6 (Swing Options)")
        print(f"  {'-'*50}")
        print(f"  Cash: ${elite_cash:>10,.2f}  |  Positions: {len(elite_positions)}")
        print(f"  Unrealized: ${elite_unrealized:>+10,.2f}")
        print(f"  Realized:   ${elite_realized:>+10,.2f}")
        print(f"  Total P&L:  ${elite_total:>+10,.2f}")
        print()

        if elite_positions:
            print(f"  {'Dir':4s} {'Sym':6s} {'Strategy':14s} {'Cost':>8s} {'P&L':>9s} {'Days':>4s}")
            for p in elite_positions:
                d = p.get("direction", "?")
                u = p.get("underlying", "?")
                desc = p.get("description", p.get("strategy_type", "?"))
                if len(desc) > 14:
                    desc = desc[:13] + "."
                cost = p.get("entry_cost", 0)
                ed = p.get("entry_date", "")
                days = (date.today() - date.fromisoformat(ed)).days if ed else 0

                current = get_position_value(client, p)
                if current is not None:
                    if p.get("legs"):
                        qty = p["legs"][0]["qty"]
                    else:
                        qty = p.get("qty", 1)
                    pnl = current * qty * 100 - cost
                else:
                    pnl = 0

                print(
                    f"  {d:4s} {u:6s} {desc:14s} "
                    f"${cost:>7,.2f} ${pnl:>+8,.2f} {days:>4}d"
                )
        print()

        # ══════════════════════════════════════
        # SCALPER SECTION
        # ══════════════════════════════════════
        scalp_eq = 25000
        scalp_cash = scalp_eq
        scalp_positions = []
        scalp_today_trades = 0
        scalp_today_pnl = 0
        scalp_total_pnl = 0
        scalp_total_trades = 0
        scalp_wr = 0

        sp = "config/paper_scalp.json"
        if os.path.exists(sp):
            with open(sp) as f:
                sdata = json.load(f)
            scalp_eq = sdata.get("equity", 25000)
            scalp_cash = sdata.get("cash", scalp_eq)
            scalp_positions = [
                p for p in sdata.get("positions", [])
                if p.get("status") == "OPEN"
            ]
            history = sdata.get("history", [])
            scalp_total_pnl = sum(t.get("pnl", 0) for t in history)
            scalp_total_trades = len(history)
            wins = [t for t in history if t.get("pnl", 0) > 0]
            scalp_wr = len(wins) / max(len(history), 1)

            today = date.today().isoformat()
            ds = sdata.get("daily_stats", {}).get(today, {})
            scalp_today_trades = ds.get("trades", 0)
            scalp_today_pnl = ds.get("pnl", 0)

        print("  0DTE SCALPER (Intraday)")
        print(f"  {'-'*50}")
        print(f"  Cash: ${scalp_cash:>10,.2f}  |  Positions: {len(scalp_positions)}")
        print(
            f"  Today: {scalp_today_trades} trades | "
            f"P&L: ${scalp_today_pnl:>+,.2f}"
        )
        print(
            f"  Total: {scalp_total_trades} trades | "
            f"WR: {scalp_wr:.0%} | "
            f"P&L: ${scalp_total_pnl:>+,.2f}"
        )
        print()

        if scalp_positions:
            print(f"  {'Dir':4s} {'Sym':5s} {'Type':14s} {'Strike':>7s} {'Cost':>8s} {'Held':>6s}")
            for p in scalp_positions:
                d = p.get("direction", "?")
                s = p.get("symbol", "?")
                st = p.get("signal_type", "?")
                strike = p.get("strike", 0)
                cost = p.get("entry_cost", 0)
                held = ""
                if p.get("entry_time"):
                    et = datetime.fromisoformat(p["entry_time"])
                    mins = (datetime.now() - et).total_seconds() / 60
                    held = f"{mins:.0f}min"
                print(
                    f"  {d:4s} {s:5s} {st:14s} "
                    f"${strike:>6} ${cost:>7,.2f} {held:>6s}"
                )
        print()

        # ══════════════════════════════════════
        # COMBINED TOTALS
        # ══════════════════════════════════════
        combined_pnl = elite_total + scalp_total_pnl + scalp_today_pnl
        combined_equity = elite_eq + scalp_eq

        print("  COMBINED")
        print(f"  {'-'*50}")
        print(f"  Total Capital:  ${combined_equity:>10,.2f}")
        print(f"  Total P&L:      ${combined_pnl:>+10,.2f}")
        print(f"  Total Return:   {combined_pnl/combined_equity:>+10.1%}")
        print()
        print("=" * 72)

        if not auto_refresh:
            break

        print(f"  Auto-refreshing every 30 seconds. Press Ctrl+C to exit.")
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            print("\n  Dashboard closed.")
            break


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="Show once without refreshing")
    args = parser.parse_args()
    dashboard(auto_refresh=not args.once)
