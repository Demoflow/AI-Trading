"""
Aggressive Live v5.3 - Stale Trade Detection.
- Detects trade files older than 1 day
- Auto-rescans if stale
- Pre-market filter
- Smart entry timing
- Multi-leg execution
- Theta-aware exits
"""

import os
import sys
import json
import time
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def is_market_hours():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    h = now.hour + now.minute / 60.0
    return 8.5 <= h <= 16.08


def get_option_value(client, sym):
    try:
        resp = client.get_quote(sym)
        if resp and resp.status_code == 200:
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


def get_spread_value(client, pos):
    legs = pos.get("legs", [])
    if legs:
        total = 0
        for leg in legs:
            sym = leg.get("symbol", "")
            val = get_option_value(client, sym)
            if val is None:
                return None
            if leg["leg"] == "LONG":
                total += val
            else:
                total -= val
        return round(total, 2)
    csym = pos.get("contract", "")
    if csym:
        return get_option_value(client, csym)
    return None


def load_trades_with_freshness_check(client, equity):
    """Load trades, auto-rescan if stale."""
    tp = "config/aggressive_trades.json"

    if not os.path.exists(tp):
        logger.info("No trade file found. Running fresh scan...")
        return run_fresh_scan(client, equity)

    with open(tp) as f:
        data = json.load(f)

    trade_date = data.get("date", "")
    trades = data.get("trades", [])
    evening_vix = data.get("vix", 20)

    # Check freshness
    if trade_date:
        try:
            td = date.fromisoformat(trade_date)
            age = (date.today() - td).days

            if age > 1:
                logger.warning(
                    f"Trade file is {age} days old ({trade_date}). "
                    f"Running fresh scan..."
                )
                return run_fresh_scan(client, equity)

            if age == 1:
                logger.info(
                    f"Trade file from yesterday ({trade_date}). "
                    f"Using as planned."
                )
        except ValueError:
            pass

    if not trades:
        logger.info("Trade file has 0 trades. Running fresh scan...")
        return run_fresh_scan(client, equity)

    return trades, evening_vix


def run_fresh_scan(client, equity):
    """Run a fresh aggressive scan."""
    try:
        from aggressive.aggressive_scanner import AggressiveScanner
        scanner = AggressiveScanner(client, equity)
        trades = scanner.run()

        # Reload the saved file for VIX
        vix = 20
        tp = "config/aggressive_trades.json"
        if os.path.exists(tp):
            with open(tp) as f:
                data = json.load(f)
            vix = data.get("vix", 20)

        return trades, vix
    except Exception as e:
        logger.error(f"Fresh scan failed: {e}")
        return [], 20


def run(paper=True):
    from utils.logging_setup import setup_logging
    setup_logging()

    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.error(f"Schwab required: {e}")
        return

    ah = os.getenv("SCHWAB_ACCOUNT_HASH", "")
    eq = float(os.getenv("ACCOUNT_EQUITY", "8000"))

    from aggressive.options_executor import OptionsExecutor
    from aggressive.smart_entry import SmartEntry
    from aggressive.exit_manager import ExitManager
    from aggressive.bracket_stops import BracketStopManager
    from aggressive.portfolio_analyst import PortfolioAnalyst
    from aggressive.greeks_exit import GreeksExitMonitor
    from aggressive.account_manager import AccountManager
    from aggressive.position_correlation import PositionCorrelation
    from aggressive.premarket_filter import PremarketFilter

    vix = 20
    try:
        import httpx
        resp = client.get_quote("$VIX")
        if resp.status_code == httpx.codes.OK:
            vix = resp.json().get(
                "$VIX", {}
            ).get("quote", {}).get("lastPrice", 20)
    except Exception:
        pass

    executor = OptionsExecutor(client, ah, paper_mode=paper)
    smart = SmartEntry(client)
    exits = ExitManager()
    bracket_mgr = BracketStopManager(client)

    def is_same_day_entry(symbol, option_symbol=""):
        """Check if a position was entered today (day trade protection)."""
        from datetime import date as _d
        today = _d.today().isoformat()
        try:
            _ah = client.get_account_numbers().json()[1]["hashValue"]
            _orders = client.get_orders_for_account(_ah).json()
            for o in _orders:
                if o.get("status") != "FILLED":
                    continue
                for leg in o.get("orderLegCollection", []):
                    if leg.get("instruction") == "BUY_TO_OPEN":
                        osym = leg.get("instrument", {}).get("symbol", "")
                        if symbol in osym or option_symbol == osym:
                            ct = o.get("closeTime", "")
                            if ct and today in ct:
                                return True
        except Exception:
            pass
        return False
    portfolio_analyst = PortfolioAnalyst(client)
    last_analyst_run = 0
    greeks_monitor = GreeksExitMonitor(client)
    acct_mgr = AccountManager(client)
    pos_corr = PositionCorrelation()
    premarket = PremarketFilter(client)

    # Load trades with freshness check
    trades, evening_vix = load_trades_with_freshness_check(client, eq)

    if not trades:
        logger.info("No trades available. System will wait for evening scan.")
        return

    logger.info(f"Loaded {len(trades)} trades (evening VIX: {evening_vix:.1f})")

    # Pre-market filter
    logger.info("=" * 60)
    logger.info("PRE-MARKET VALIDATION")
    logger.info("=" * 60)

    valid_trades, skipped = premarket.check_all_trades(trades, evening_vix)

    if skipped:
        for t, reason in skipped:
            logger.info(f"  SKIP: {t['direction']} {t['symbol']} - {reason}")

    trades = valid_trades

    if not trades:
        logger.info("All trades invalidated. No trades today.")
        return

    executed = set()
    cycle = 0
    midday_done = False

    logger.info("=" * 60)
    logger.info(f"ELITE MODE {'PAPER' if paper else 'LIVE'} v5.3")
    logger.info(
        f"Valid trades: {len(trades)} | "
        f"Equity: ${eq:,.2f} | VIX: {vix:.1f}"
    )
    logger.info("=" * 60)

    for t in trades:
        s = t.get("strategy", {})
        pm = t.get("_premarket", {})
        mod = pm.get("size_modifier", 1.0)
        mod_str = f" (size:{mod:.0%})" if mod != 1.0 else ""
        logger.info(
            f"  {t['direction']} {t['symbol']} "
            f"{s.get('type', '?')} "
            f"{s.get('description', '')} "
            f"${s.get('total_cost', 0):,.2f}{mod_str}"
        )

    while True:
        now = datetime.now()

        if not is_market_hours():
            if now.hour >= 16 and now.minute > 5:
                logger.info("Market closed.")
                s = executor.get_summary()
                if s:
                    logger.info(f"  Cash: ${s['cash']:,.2f}")
                    logger.info(f"  Deployed: ${s['deployed']:,.2f}")
                    logger.info(f"  Open: {s['open_positions']}")
                    logger.info(f"  P&L: ${s['total_pnl']:+,.2f}")
                break

            if cycle % 12 == 0:
                logger.info(f"Waiting... ({now.strftime('%H:%M')})")
            cycle += 1
            time.sleep(30)
            continue

        cycle += 1
        hour = now.hour + now.minute / 60.0

        # ── ENTRIES (10:00 - 14:30) ──
        if 10.0 <= hour <= 14.5:
            for trade in trades:
                sym = trade["symbol"]
                if sym in executed:
                    continue

                s = trade.get("strategy", {})
                contracts = s.get("contracts", [])
                if not contracts:
                    continue

                csym = contracts[0].get("symbol", "")
                direction = trade["direction"]

                should_buy, limit, reason = smart.should_enter(
                    sym, direction, csym
                )

                if trade.get("_rejected"):
                    continue
                if should_buy:
                    # Check real cash before ordering
                    try:
                        r0 = client.get_account_numbers()
                        ah0 = r0.json()[1]["hashValue"]  # Brokerage account
                        r1 = client.get_account(ah0)
                        real_cash = r1.json().get("securitiesAccount",{}).get("currentBalances",{}).get("availableFundsNonMarginableTrade",0)
                        trade_cost = trade.get("strategy",{}).get("total_cost",2000)
                        if trade_cost > real_cash:
                            logger.warning(f"SKIP {sym}: cost ${trade_cost:,.0f}  ${real_cash:,.0f}")
                            trade["_rejected"] = True
                            continue
                    except Exception as e:
                        logger.warning(f"Cash check failed: {e}")
                    pm = trade.get("_premarket", {})
                    mod = pm.get("size_modifier", 1.0)
                    if mod < 1.0:
                        logger.info(
                            f"SIZE ADJ: {sym} {mod:.0%} "
                            f"({pm.get('reason', '')})"
                        )

                    logger.info(
                        f"ENTRY: {s.get('type', '?')} {sym} "
                        f"{s.get('description', '')} ({reason})"
                    )
                    result = executor.execute_strategy(trade)

                    if result.get("status") in ("SUBMITTED", "FILLED"):
                        executed.add(sym)
                        logger.info(f"ENTERED: {sym}")
                    # Place GTC stop at broker level
                    try:
                        if not paper:
                            s = trade.get("strategy", {})
                            contracts = s.get("contracts", [])
                            if contracts:
                                entry_mid = contracts[0].get("mid", 0)
                                csym = contracts[0].get("symbol", "")
                                qty = contracts[0].get("qty", 1)
                                stype = s.get("type", "NAKED_LONG")
                                if entry_mid > 0 and csym:
                                    ah_stop = client.get_account_numbers().json()[1]["hashValue"]
                                    bracket_mgr.place_stop(csym, qty, entry_mid, stype, ah_stop)
                    except Exception as e:
                        logger.warning(f"Bracket stop error: {e}")
                    else:
                        logger.warning(f"FAILED {sym}: {result}")
                        trade["_rejected"] = True

        # ── MID-DAY RE-SCAN (12:30) ──
        if 12.45 <= hour <= 12.55 and not midday_done:
            midday_done = True
            logger.info("MID-DAY SCAN...")
            try:
                from aggressive.aggressive_scanner import AggressiveScanner
                scanner = AggressiveScanner(client, eq)
                new = scanner.run()
                for nt in new:
                    if nt["symbol"] not in executed:
                        if not any(
                            t["symbol"] == nt["symbol"] for t in trades
                        ):
                            trades.append(nt)
                            logger.info(
                                f"NEW: {nt['direction']} {nt['symbol']}"
                            )
            except Exception as e:
                logger.warning(f"Midday: {e}")

        # ── EXITS ──
        if paper:
            positions = executor.paper_positions.get("positions", [])
            for pos in positions:
                if pos.get("status") != "OPEN":
                    continue

                stype = pos.get("strategy_type", "NAKED_LONG")
                entry_cost = pos.get("entry_cost", 0)

                current = get_spread_value(client, pos)
                if current is None:
                    continue

                pos["highest_val"] = max(
                    pos.get("highest_val", current), current
                )
                executor._save_paper()

                if stype in (
                    "NAKED_LONG", "DEBIT_SPREAD", "CALENDAR_SPREAD"
                ):
                    entry_val = pos.get("entry_net", 0)
                    if entry_val <= 0:
                        entry_val = pos.get(
                            "entry_price", entry_cost / 100
                        )
                    if entry_val <= 0:
                        continue

                    _et = pos.get("entry_time","")
                    if _et:
                        try:
                            _etd = _dt.datetime.fromisoformat(_et) if isinstance(_et,str) else _et
                            if (_dt.datetime.now()-_etd).total_seconds() < 120:
                                continue
                        except: pass
                    # Convert per-share to total value
                    qty = pos.get("legs",[{}])[0].get("qty",1) if pos.get("legs") else pos.get("qty",1)
                    current_total = current * qty * 100
                    should_exit, reason = exits.check_exit(
                        pos, current_total
                    )

                    if False:  # scale disabled
                        if not pos.get("t1_hit"):
                            pos["t1_hit"] = True
                            for leg in pos.get("legs", []):
                                leg["qty"] = max(1, leg["qty"] // 2)
                            executor.paper_positions["cash"] += (
                                entry_cost * 0.25
                            )
                            executor._save_paper()
                            logger.info(
                                f"SCALE: {pos['underlying']} {reason}"
                            )

                    elif should_exit :
                        executor.close_position(
                            pos, max(current, 0.01)
                        )
                        logger.info(
                            f"EXIT: {pos['underlying']} {reason}"
                        )

                elif stype == "CREDIT_SPREAD":
                    entry_credit = pos.get("entry_net", 0)
                    close_cost = abs(current)

                    if entry_credit > 0 and close_cost > entry_credit * 2:
                        executor.close_position(pos, close_cost)
                        logger.info(
                            f"CREDIT STOP: {pos['underlying']}"
                        )
                    elif entry_credit > 0 and close_cost < entry_credit * 0.25:
                        executor.close_position(pos, close_cost)
                        logger.info(
                            f"CREDIT PROFIT: {pos['underlying']}"
                        )

                    ed = pos.get("entry_date", "")
                    if ed:
                        days = (
                            date.today() - date.fromisoformat(ed)
                        ).days
                        if days >= 25:
                            executor.close_position(pos, close_cost)
                            logger.info(
                                f"CREDIT TIME: {pos['underlying']}"
                            )


        # ── LIVE EXITS ──
        if not paper:
            live_positions = executor.get_live_positions()

            # Group positions by underlying to detect spreads
            from collections import defaultdict
            by_underlying = defaultdict(list)
            for lp in live_positions:
                if lp["qty"] != 0:
                    by_underlying[lp["underlying"]].append(lp)

            for sym, positions in by_underlying.items():
                long_legs = [p for p in positions if p["qty"] > 0]
                short_legs = [p for p in positions if p["qty"] < 0]

                if long_legs and short_legs:
                    # DEBIT SPREAD: long + short on same underlying
                    total_entry = sum(abs(p["avg_price"]) * abs(p["qty"]) * 100 for p in positions)
                    total_mkt = sum(p["market_value"] for p in positions)
                    current_total = abs(total_mkt)

                    legs = []
                    for p in long_legs:
                        legs.append({"symbol": p["symbol"], "leg": "LONG", "qty": abs(p["qty"])})
                    for p in short_legs:
                        legs.append({"symbol": p["symbol"], "leg": "SHORT", "qty": abs(p["qty"])})

                    pos_for_exit = {
                        "underlying": sym,
                        "strategy_type": "DEBIT_SPREAD",
                        "entry_cost": total_entry,
                        "legs": legs,
                        "entry_date": "",
                        "max_hold_days": 21,
                    }
                    should_exit, reason = exits.check_exit(pos_for_exit, current_total)
                    if should_exit:
                        result = executor.close_position_live(pos_for_exit)
                        logger.info(f"LIVE EXIT SPREAD: {sym} {reason} -> {result.get('status')}")
                        if result.get("status") == "FILLED":
                                acct_mgr.record_trade(sym, "SPREAD", "DEBIT_SPREAD", total_entry, current_total, current_total - total_entry)

                elif long_legs:
                    # NAKED LONG
                    for lp in long_legs:
                        entry_cost = abs(lp["avg_price"]) * abs(lp["qty"]) * 100
                        if entry_cost <= 0:
                            continue
                        current_total = abs(lp["market_value"])
                        pos_for_exit = {
                            "underlying": sym,
                            "strategy_type": "NAKED_LONG",
                            "entry_cost": entry_cost,
                            "legs": [{"symbol": lp["symbol"], "leg": "LONG", "qty": abs(lp["qty"])}],
                            "entry_date": "",
                            "max_hold_days": 21,
                        }
                        should_exit, reason = exits.check_exit(pos_for_exit, current_total)
                        if should_exit:
                            # DAY_TRADE_EXIT protection
                            if is_same_day_entry(sym, lp["symbol"]):
                                if "stop_loss" not in reason:
                                    logger.warning(f"DAY TRADE BLOCKED EXIT: {sym} {reason} - entered today")
                                    continue
                                else:
                                    logger.warning(f"DAY TRADE OVERRIDE: {sym} stop loss triggered - selling anyway")
                            result = executor.close_position_live(pos_for_exit)
                            logger.info(f"LIVE EXIT: {sym} {reason} -> {result.get('status')}")

                    # Also check Greeks for naked longs still holding
                    if not should_exit and stype == "NAKED_LONG":
                        try:
                            greeks = greeks_monitor.get_option_greeks(lp["symbol"])
                            if greeks:
                                g_exit, g_reason = greeks_monitor.check_greeks_exit(pos_for_exit, greeks)
                                if g_exit:
                                    result = executor.close_position_live(pos_for_exit)
                                    logger.info(f"GREEKS EXIT: {sym} {g_reason} -> {result.get('status')}")
                        except Exception:
                            pass
                            if result.get("status") == "FILLED":
                                acct_mgr.record_trade(sym, "LONG", "NAKED_LONG", entry_cost, current_total, current_total - entry_cost)

                elif short_legs:
                    # SHORT position (premium sold)
                    for lp in short_legs:
                        entry_cost = abs(lp["avg_price"]) * abs(lp["qty"]) * 100
                        if entry_cost <= 0:
                            continue
                        current_total = abs(lp["market_value"])
                        stype = "NAKED_PUT" if "P" in lp["symbol"][-8:] else "NAKED_CALL"
                        pos_for_exit = {
                            "underlying": sym,
                            "strategy_type": stype,
                            "entry_cost": entry_cost,
                            "premium": entry_cost,
                            "legs": [{"symbol": lp["symbol"], "leg": "SHORT", "qty": abs(lp["qty"])}],
                            "entry_date": "",
                            "max_hold_days": 21,
                        }
                        should_exit, reason = exits.check_exit(pos_for_exit, current_total)
                        if should_exit:
                            result = executor.close_position_live(pos_for_exit)
                            logger.info(f"LIVE EXIT SHORT: {sym} {reason} -> {result.get('status')}")

        # ── PORTFOLIO ANALYST (every 30 min) ──
        import time as _time
        if _time.time() - last_analyst_run > 1800:  # 30 minutes
            last_analyst_run = _time.time()
            try:
                if not paper:
                    live_pos = executor.get_live_positions()
                    if live_pos:
                        opt_positions = []
                        for lp in live_pos:
                            opt_positions.append({
                                "underlying": lp["underlying"],
                                "symbol": lp["symbol"],
                                "direction": "CALL" if lp["qty"] > 0 else "PUT",
                                "strategy_type": "NAKED_LONG",
                                "entry_cost": abs(lp["avg_price"]) * abs(lp["qty"]) * 100,
                                "qty": abs(lp["qty"]),
                            })
                        analyst_results = portfolio_analyst.run_full_analysis(options_positions=opt_positions)
                        for ar in analyst_results:
                            if ar["action"] == "SELL":
                                logger.warning(f"ANALYST RECOMMENDS SELL: {ar['symbol']} - {', '.join(ar['flags'])}")
                                # DAY TRADE PROTECTION: check if position was entered today
                                from datetime import date as _date
                                today_str = _date.today().isoformat()
                                for lp in live_pos:
                                    if lp["underlying"] == ar["symbol"]:
                                        # Check trade log for same-day entry
                                        is_same_day = False
                                        try:
                                            import json as _json
                                            trades_file = "config/aggressive_trades.json"
                                            if os.path.exists(trades_file):
                                                trades_data = _json.load(open(trades_file))
                                                scan_date = trades_data.get("scan_date", "")
                                                if scan_date == today_str or scan_date == (_date.today() - __import__('datetime').timedelta(days=1)).isoformat():
                                                    # Trade was from today's or last night's scan
                                                    # Check filled orders for same-day entry
                                                    pass
                                        except Exception:
                                            pass
                                        # Check if any order for this symbol was filled today
                                        try:
                                            ah = client.get_account_numbers().json()[1]["hashValue"]
                                            orders = client.get_orders_for_account(ah).json()
                                            for o in orders:
                                                if o.get("status") == "FILLED":
                                                    legs = o.get("orderLegCollection", [])
                                                    for leg in legs:
                                                        if leg.get("instruction") == "BUY_TO_OPEN":
                                                            osym = leg.get("instrument", {}).get("symbol", "")
                                                            if ar["symbol"] in osym or lp["symbol"] == osym:
                                                                close_time = o.get("closeTime", "")
                                                                if close_time and today_str in close_time:
                                                                    is_same_day = True
                                                                    break
                                                    if is_same_day:
                                                        break
                                        except Exception:
                                            pass

                                        if is_same_day:
                                            logger.warning(f"DAY TRADE BLOCKED: {ar['symbol']} entered today - cannot sell same day")
                                            continue

                                        pos_for_close = {
                                            "underlying": lp["underlying"],
                                            "strategy_type": "NAKED_LONG",
                                            "legs": [{"symbol": lp["symbol"], "leg": "LONG", "qty": abs(lp["qty"])}],
                                        }
                                        result = executor.close_position_live(pos_for_close)
                                        logger.info(f"ANALYST SOLD: {ar['symbol']} -> {result.get('status')}")
                                        break
            except Exception as e:
                logger.warning(f"Portfolio analyst error: {e}")

        # ── ORDER FILL CHECK ──
        try:
            if not paper and hasattr(executor, 'fill_tracker') and executor.fill_tracker:
                ah_fill = executor._get_account_hash()
                fills = executor.fill_tracker.check_fills(ah_fill)
                if fills:
                    for fill in fills:
                        logger.info(f"CONFIRMED FILL: {fill['symbol']} {fill['status']}")
                pending = executor.fill_tracker.get_pending_count()
                if pending > 0 and cycle % 5 == 0:
                    logger.info(f"Pending orders: {pending}")
        except Exception:
            pass

        # ── STATUS ──
        if cycle % 10 == 0:
            s = executor.get_live_summary() if not paper else executor.get_summary()
            if s:
                logger.info(
                    f"[{now.strftime('%H:%M')}] "
                    f"Open:{s['open_positions']} "
                    f"Exec:{len(executed)}/{len(trades)} "
                    f"Cash:${s['cash']:,.0f} "
                    f"PnL:${s['total_pnl']:+,.0f}"
                )

        time.sleep(30)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if args.live:
        logger.warning("=" * 60)
        logger.warning("LIVE MODE - REAL MONEY")
        logger.warning("=" * 60)
        logger.warning("AUTONOMOUS LIVE MODE ACTIVE")

    run(paper=not args.live)