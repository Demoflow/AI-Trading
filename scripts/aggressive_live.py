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
            _ah = next(a["hashValue"] for a in client.get_account_numbers().json() if a["accountNumber"] == "28135437")
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

    # PDT pre-check: detect if account is restricted
    pdt_active = False
    if not paper:
        try:
            _pdt_summ = executor.get_live_summary()
            # Check if there's a day trade equity call or restriction
            # Schwab doesn't expose day_trades_left directly
            # We detect PDT by checking if equity < $25K on a margin account
            if _pdt_summ:
                _eq = _pdt_summ.get("equity", 0)
                if _eq < 25000 and _eq > 0:
                    logger.warning(f"PDT WARNING: Equity ${_eq:,.2f} < $25K — only multi-day holds allowed")
                    logger.warning("PDT: System will trade but day trade protection is ACTIVE")
        except Exception:
            pass

    # Run exit manager self-test before trading
    from aggressive.exit_manager import ExitManager as _EM
    _test_em = _EM()
    if not _test_em.self_test():
        logger.error("EXIT MANAGER FAILED SELF-TEST — ABORTING")
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
    # Track entry dates for live positions (for max_hold_days)
    _entry_dates = {}
    _peak_pnl = {}  # Track peak P&L % per symbol for trailing stops
    try:
        import json as _j
        _entry_dates = _j.load(open("config/entry_dates.json"))
    except Exception:
        pass
    # Skip trades already marked as entered from previous runs
    for t in trades:
        if t.get("_entered") or t.get("_rejected"):
            executed.add(t["symbol"])
    # Pre-populate executed from live positions to prevent double-entry
    if not paper:
        try:
            live_pos = executor.get_live_positions()
            held_syms = set(p['underlying'] for p in live_pos)
            for t in trades:
                if t['symbol'] in held_syms:
                    executed.add(t['symbol'])
                    logger.info(f"ALREADY HELD: {t['symbol']} - skipping entry")
        except Exception as e:
            logger.warning(f"Position check failed: {e}")
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

                # Check cash before entry
                try:
                    if not paper:
                        _summ = executor.get_live_summary()
                        if _summ:
                            # Use settled cash only (cash account rules)
                            _settled = _summ.get("settled_cash", _summ.get("cash", 0))
                            _cost = trade.get("strategy", {}).get("total_cost", 500)
                            _buffer = 500  # Keep $500 safety buffer
                            if _cost > (_settled - _buffer):
                                logger.warning(f"SKIP {sym}: cost ${_cost:.0f} > settled cash ${_settled:.0f} (buffer ${_buffer})")
                                trade["_rejected"] = True
                                continue
                except Exception:
                    pass

                # MAX POSITION SIZE CHECK
                MAX_POSITION_PCT = 0.10
                _trade_cost = trade.get("strategy", {}).get("total_cost", 500)
                _equity = executor.get_live_summary().get("equity", 7500) if not paper else 7500
                try:
                    _summ2 = executor.get_live_summary()
                    if _summ2:
                        _equity = _summ2.get("equity", 7611)
                except Exception:
                    pass
                if _trade_cost > _equity * MAX_POSITION_PCT:
                    logger.warning(f"SIZE BLOCK: {sym} cost ${_trade_cost:.0f} > {MAX_POSITION_PCT:.0%} of ${_equity:.0f}")
                    trade["_rejected"] = True
                    continue

                should_buy, limit, reason = smart.should_enter(
                    sym, direction, csym
                )

                if trade.get("_rejected"):
                    continue
                if should_buy:
                    # Check real cash before ordering
                    try:
                        ah0 = next(a["hashValue"] for a in client.get_account_numbers().json() if a["accountNumber"] == os.getenv("SCHWAB_ACCOUNT_NUMBER", "28135437"))  # Brokerage account
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
                        # Track entry date
                        from datetime import date
                        _entry_dates[sym] = date.today().isoformat()
                        try:
                            json.dump(_entry_dates, open("config/entry_dates.json", "w"), indent=2)
                        except Exception:
                            pass
                        # Mark trade as entered
                        trade["_entered"] = True
                        trade["_entered_time"] = str(datetime.now())
                        # Save to file
                        try:
                            _tf = json.load(open("config/aggressive_trades.json"))
                            for _t in _tf.get("trades", []):
                                if _t.get("symbol") == sym:
                                    _t["_entered"] = True
                            json.dump(_tf, open("config/aggressive_trades.json", "w"), indent=2, default=str)
                        except Exception:
                            pass
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
                                    ah_stop = next(a["hashValue"] for a in client.get_account_numbers().json() if a["accountNumber"] == "28135437")
                                    # Wait for Schwab to process entry before placing stop
                                    import time as _tw
                                    _tw.sleep(3)
                                    bracket_mgr.place_stop(csym, qty, entry_mid, stype, ah_stop)
                    except Exception as e:
                        logger.warning(f"Bracket stop error: {e}")
                    if result.get("status") == "REJECTED":
                        _reason = str(result.get("reason", ""))
                        if "Day Trade" in _reason or "Equity Restricted" in _reason:
                            logger.error(f"PDT RESTRICTION DETECTED — halting all entries")
                            break  # Stop trying to enter any more trades
                    if result.get("status") == "SUBMITTED":
                        logger.info(f"SUBMITTED {sym}: order placed, awaiting fill")
                        executed.add(sym)
                    elif result.get("status") not in ("FILLED", "SUBMITTED"):
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
                            _etd = datetime.fromisoformat(_et) if isinstance(_et,str) else _et
                            if (datetime.now()-_etd).total_seconds() < 120:
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
                    # Calendar/spread entry cost = NET debit (not sum of both legs)
                    long_cost = sum(abs(p["avg_price"]) * abs(p["qty"]) * 100 for p in long_legs)
                    short_credit = sum(abs(p["avg_price"]) * abs(p["qty"]) * 100 for p in short_legs)
                    total_entry = abs(long_cost - short_credit)  # Net debit paid
                    if total_entry < 10:
                        total_entry = 100  # Minimum to avoid div-by-zero
                    # Net market value (long - short)
                    total_mkt = sum(p["market_value"] for p in positions)  # Short legs are already negative
                    current_total = abs(total_mkt)

                    legs = []
                    for p in long_legs:
                        legs.append({"symbol": p["symbol"], "leg": "LONG", "qty": abs(p["qty"])})
                    for p in short_legs:
                        legs.append({"symbol": p["symbol"], "leg": "SHORT", "qty": abs(p["qty"])})

                    # Detect calendar vs debit spread
                    is_calendar = False
                    if long_legs and short_legs:
                        # Calendar = same strike, different expiration
                        # Debit spread = same expiration, different strike
                        long_sym = long_legs[0]["symbol"]
                        short_sym = short_legs[0]["symbol"]
                        # Option symbols: AAPL  260417C00150000
                        # Strike is last 8 chars, expiry is chars 6-12
                        try:
                            long_strike = long_sym[-8:]
                            short_strike = short_sym[-8:]
                            if long_strike == short_strike:
                                is_calendar = True
                        except Exception:
                            pass
                    spread_type = "CALENDAR_SPREAD" if is_calendar else "DEBIT_SPREAD"
                    pos_for_exit = {
                        "underlying": sym,
                        "strategy_type": spread_type,
                        "entry_cost": total_entry,
                        "legs": legs,
                        "entry_date": _entry_dates.get(sym, ""),
                        "max_hold_days": 21,
                    }
                    # Check cooldown before exit
                    if hasattr(run, "_exit_cooldowns") and sym in run._exit_cooldowns:
                        import datetime as _dtc
                        _age = (_dtc.datetime.now() - run._exit_cooldowns[sym]).total_seconds()
                        if _age < 600:
                            should_exit, reason = False, "cooldown"
                        else:
                            del run._exit_cooldowns[sym]
                            should_exit, reason = exits.check_exit(pos_for_exit, current_total)
                    else:
                        should_exit, reason = exits.check_exit(pos_for_exit, current_total)
                    if should_exit:
                        if is_same_day_entry(sym):
                            logger.warning(f"DAY TRADE BLOCKED (spread exit): {sym} entered today")
                            should_exit = False
                    if should_exit:
                        result = executor.close_position_live(pos_for_exit)
                        logger.info(f"LIVE EXIT SPREAD: {sym} {reason} -> {result.get('status')}")
                        if result.get("status") == "REJECTED":
                            # Don't retry for 10 minutes
                            import datetime as _dtmod
                            if not hasattr(run, '_exit_cooldowns'):
                                run._exit_cooldowns = {}
                            run._exit_cooldowns[sym] = _dtmod.datetime.now()
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
                            "entry_date": _entry_dates.get(sym, ""),
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
                    if not should_exit and pos_for_exit.get("strategy_type","") == "NAKED_LONG":
                        try:
                            greeks = greeks_monitor.get_option_greeks(lp["symbol"])
                            if greeks:
                                g_exit, g_reason = greeks_monitor.check_greeks_exit(pos_for_exit, greeks)
                                if g_exit:
                                    result = executor.close_position_live(pos_for_exit)
                                    logger.info(f"GREEKS EXIT: {sym} {g_reason} -> {result.get('status')}")
                                    if result.get("status") == "FILLED":
                                        acct_mgr.record_trade(sym, "LONG", "NAKED_LONG", entry_cost, current_total, current_total - entry_cost)
                        except Exception:
                            pass

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
                            "entry_date": _entry_dates.get(sym, ""),
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
                                            ah = next(a["hashValue"] for a in client.get_account_numbers().json() if a["accountNumber"] == "28135437")
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

        # ── PEAK EQUITY TRACKING ──
        if not paper and cycle % 20 == 0:  # Every ~10 min
            try:
                _bal = client.get_account(next(a["hashValue"] for a in client.get_account_numbers().json() if a["accountNumber"] == "28135437"))
                _eq = _bal.json().get("securitiesAccount", {}).get("currentBalances", {}).get("liquidationValue", 0)
                if _eq > 0:
                    _bs_path = "config/breaker_state.json"
                    if os.path.exists(_bs_path):
                        _bs = json.load(open(_bs_path))
                        if _eq > _bs.get("peak", 0):
                            _bs["peak"] = _eq
                            _bs["last_updated"] = date.today().isoformat()
                            json.dump(_bs, open(_bs_path, "w"), indent=2)
                            logger.info(f"NEW PEAK EQUITY: ${_eq:,.2f}")
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