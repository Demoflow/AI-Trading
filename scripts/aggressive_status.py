"""
Status v5.1 - Backward Compatible.
Handles old single-leg and new multi-leg positions.
"""

import os
import sys
import json
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    """Get current value - handles both old and new format."""
    # New format: multi-leg with legs array
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

    # Old format: single contract field
    csym = pos.get("contract", "")
    if csym:
        return get_live_value(client, csym)

    return None


def get_underlying_price(client, symbol):
    if not client or not symbol:
        return None
    try:
        import httpx
        resp = client.get_quote(symbol)
        if resp.status_code == httpx.codes.OK:
            data = resp.json()
            q = data.get(symbol, {}).get("quote", {})
            return q.get("lastPrice", 0)
    except Exception:
        pass
    return None


def status():
    eq = float(os.environ.get("ACCOUNT_EQUITY", "8000"))

    client = None
    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
    except Exception:
        pass

    pp = "config/paper_options.json"
    if os.path.exists(pp):
        with open(pp) as f:
            data = json.load(f)

        cash = data.get("cash", 0)
        positions = data.get("positions", [])
        open_pos = [p for p in positions if p["status"] == "OPEN"]
        closed = [p for p in positions if p["status"] == "CLOSED"]

        total_current = 0
        total_entry = 0
        unrealized = 0
        live_data = []

        for p in open_pos:
            cost = p.get("entry_cost", 0)
            total_entry += cost
            stype = p.get("strategy_type", "NAKED_LONG")

            current = get_position_value(client, p) if client else None
            stock_price = get_underlying_price(client, p.get("underlying", "")) if client else None

            if current is not None and current > 0:
                if stype == "CREDIT_SPREAD":
                    credit = p.get("credit_received", 0)
                    qty = p["legs"][0]["qty"] if p.get("legs") else 1
                    close_cost = abs(current) * qty * 100
                    cur_val = cost + (credit - close_cost)
                    pnl = credit - close_cost
                else:
                    # Old format: single contract
                    if p.get("legs"):
                        qty = p["legs"][0]["qty"]
                    else:
                        qty = p.get("qty", 1)
                    cur_val = current * qty * 100
                    pnl = cur_val - cost

                pnl_pct = pnl / max(cost, 1)
            elif current is not None and stype in ("DEBIT_SPREAD", "CALENDAR_SPREAD"):
                # Spread might have small or zero value
                qty = p["legs"][0]["qty"] if p.get("legs") else 1
                cur_val = max(current, 0) * qty * 100
                pnl = cur_val - cost
                pnl_pct = pnl / max(cost, 1)
            else:
                cur_val = cost
                pnl = 0
                pnl_pct = 0

            total_current += cur_val
            unrealized += pnl
            live_data.append({
                "pos": p,
                "cur_val": cur_val,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "stock_price": stock_price,
                "current_per": current,
            })

        realized = sum(p.get("pnl", 0) for p in closed)
        total_pnl = realized + unrealized
        total_val = cash + total_current

        print()
        print("=" * 70)
        print("  ELITE OPTIONS SYSTEM - LIVE STATUS")
        print("  " + ("Real-time Schwab quotes" if client else "Offline"))
        print("=" * 70)
        print(f"  Starting Equity:   ${eq:>12,.2f}")
        print(f"  Cash:              ${cash:>12,.2f}")
        print(f"  Positions:         ${total_current:>12,.2f}")
        print(f"  Portfolio:         ${total_val:>12,.2f}")
        print()
        print(f"  Unrealized P&L:    ${unrealized:>+12,.2f}")
        print(f"  Realized P&L:      ${realized:>+12,.2f}")
        print(f"  TOTAL P&L:         ${total_pnl:>+12,.2f}")
        print(f"  RETURN:            {(total_val - eq) / eq:>+12.1%}")
        print()

        if live_data:
            print(f"  POSITIONS ({len(open_pos)}):")
            print(
                f"  {'Dir':4s} {'Sym':6s} {'Strategy':14s} "
                f"{'Stock':>7s} {'Cost':>8s} {'Value':>8s} "
                f"{'P&L':>9s} {'%':>6s} {'Days':>4s}"
            )
            print(f"  {'-'*72}")

            for ld in live_data:
                p = ld["pos"]
                d = p.get("direction", "?")
                u = p.get("underlying", "?")
                st = p.get("strategy_type", "NAKED")
                desc = p.get("description", st)
                cost = p.get("entry_cost", 0)
                ed = p.get("entry_date", "")
                days = (date.today() - date.fromisoformat(ed)).days if ed else 0
                sp = ld.get("stock_price") or 0

                if len(desc) > 14:
                    desc = desc[:13] + "."

                print(
                    f"  {d:4s} {u:6s} {desc:14s} "
                    f"${sp:>6.2f} ${cost:>7,.2f} ${ld['cur_val']:>7,.2f} "
                    f"${ld['pnl']:>+8,.2f} {ld['pnl_pct']:>+5.0%} {days:>4}d"
                )

                # Show legs for multi-leg positions
                if p.get("legs"):
                    for leg in p["legs"]:
                        lv = get_live_value(client, leg.get("symbol", "")) if client else None
                        lv_str = f"${lv:.2f}" if lv else "n/a"
                        ep = leg.get("price", 0)
                        print(
                            f"       {leg['leg']:5s} "
                            f"${leg.get('strike', 0):>7} "
                            f"x{leg.get('qty', 0)} "
                            f"entry=${ep:.2f} now={lv_str}"
                        )
                # Show old format contract
                elif p.get("contract"):
                    ep = p.get("entry_price", 0)
                    cv = ld.get("current_per")
                    cv_str = f"${cv:.2f}" if cv else "n/a"
                    print(
                        f"       LONG  ${p.get('strike', 0):>7} "
                        f"x{p.get('qty', 1)} "
                        f"entry=${ep:.2f} now={cv_str}"
                    )

            print(f"  {'-'*72}")
            print(
                f"  {'':4s} {'TOTAL':6s} {'':14s} "
                f"{'':>7s} ${total_entry:>7,.2f} ${total_current:>7,.2f} "
                f"${unrealized:>+8,.2f}"
            )
        else:
            print("  No open positions")

        if closed:
            wins = [p for p in closed if p.get("pnl", 0) > 0]
            losses = [p for p in closed if p.get("pnl", 0) <= 0]
            wr = len(wins) / len(closed) if closed else 0
            avg_w = sum(p["pnl"] for p in wins) / len(wins) if wins else 0
            avg_l = sum(p["pnl"] for p in losses) / len(losses) if losses else 0
            print()
            print(f"  HISTORY ({len(closed)} trades):")
            print(f"  W:{len(wins)} L:{len(losses)} WR:{wr:.0%} Avg W:${avg_w:+,.2f} Avg L:${avg_l:+,.2f}")
            print(f"  Realized: ${realized:+,.2f}")
            for p in closed[-5:]:
                r = "W" if p.get("pnl", 0) > 0 else "L"
                st = p.get("strategy_type", "?")
                desc = p.get("description", st)
                if len(desc) > 20:
                    desc = desc[:19] + "."
                print(
                    f"    [{r}] {p.get('direction','?')} "
                    f"{p.get('underlying','?')} "
                    f"{desc} "
                    f"${p.get('pnl',0):+,.2f}"
                )

        print("=" * 70)
    else:
        print("No portfolio. Run evening scan first.")

    # Pending trades
    tp = "config/aggressive_trades.json"
    if os.path.exists(tp):
        with open(tp) as f:
            data = json.load(f)
        trades = data.get("trades", [])
        if trades:
            regime = data.get("regime", "?")
            vix = data.get("vix", 0)
            print()
            print(f"  PENDING ({len(trades)}) | Regime: {regime} | VIX: {vix:.1f}")
            print(f"  {'Dir':4s} {'Sym':6s} {'Strategy':16s} {'Details':22s} {'Cost':>9s} {'Max P/L':>12s}")
            print(f"  {'-'*72}")
            for t in trades:
                s = t.get("strategy", {})
                stype = s.get("type", "?")
                desc = s.get("description", "")
                if len(desc) > 22:
                    desc = desc[:21] + "."
                mp = s.get("max_profit", "?")
                ml = s.get("max_loss", "?")
                if isinstance(mp, (int, float)):
                    mp_str = f"+${mp:.2f}"
                else:
                    mp_str = str(mp)
                if isinstance(ml, (int, float)):
                    ml_str = f"-${ml:.2f}"
                else:
                    ml_str = str(ml)
                print(
                    f"  {t['direction']:4s} {t['symbol']:6s} "
                    f"{stype:16s} {desc:22s} "
                    f"${s.get('total_cost', 0):>8,.2f} "
                    f"{mp_str}/{ml_str}"
                )
                # Show legs
                for c in s.get("contracts", []):
                    print(
                        f"       {c.get('leg', '?'):5s} "
                        f"${c.get('strike', 0):>7} "
                        f"{c.get('dte', 0)}DTE "
                        f"d={c.get('delta', 0)} "
                        f"${c.get('mid', 0):.2f}"
                    )
            print(f"  Total: ${data.get('total_cost', 0):,.2f} ({data.get('deployment_pct', 0)}%)")
    print()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    status()
