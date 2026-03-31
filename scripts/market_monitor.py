"""
Persistent Market Hours Monitor - Enhanced.
Uses all 20 improvements.
"""

import os
import sys
import time
import json
from datetime import datetime, date

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

from loguru import logger
from dotenv import load_dotenv

load_dotenv()

LEVERED = {
    "TQQQ", "SQQQ", "UPRO", "SPXU", "SOXL",
    "SOXS", "LABU", "TNA", "TZA", "NUGT",
}


def is_market_hours():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    h = now.hour + now.minute / 60.0
    return 9.5 <= h <= 16.08


def run_loop(paper=True):
    from utils.logging_setup import setup_logging
    setup_logging()
    from utils.market_calendar import MarketCalendar
    from utils.equity_sync import EquitySync
    from utils.premarket_scanner import PremarketScanner
    from utils.trade_logger import TradeLogger
    from utils.slippage_tracker import SlippageTracker
    from utils.correlation_sizer import CorrelationSizer
    from utils.volume_fetcher import VolumeFetcher
    from utils.spy_correlation import SPYCorrelationMonitor
    from utils.options_selector import OptionsSelector
    from strategy.portfolio_manager import PortfolioManager
    from strategy.exit_engine import ExitEngine
    from strategy.entry_optimizer import EntryOptimizer
    from risk.pre_trade_validator import PreTradeValidator
    from risk.circuit_breakers import IndependentCircuitBreakers
    from alerts.notifier import Notifier
    from strategy.position_tracker import Position
    from analysis.signals.signal_generator import SignalGenerator
    from analysis.scoring.options_flow_score import OptionsFlowScorer
    from data.broker.schwab_executor import SchwabExecutor

    cal = MarketCalendar()
    if not cal.is_market_open_today():
        logger.info("Market closed today.")
        return

    # Sync equity (#20)
    schwab_client = None
    if not paper:
        from data.broker.schwab_auth import get_schwab_client
        from data.broker.schwab_auth import get_account_hash
        schwab_client = get_schwab_client()
        ah = os.getenv("SCHWAB_ACCOUNT_HASH")
        if not ah:
            ah = get_account_hash(schwab_client)
        exe = SchwabExecutor(schwab_client, ah, paper_mode=False)
    else:
        exe = SchwabExecutor(paper_mode=True)

    esync = EquitySync()
    eq = esync.get_real_equity(schwab_client)

    pm = PortfolioManager(eq)
    exit_eng = ExitEngine(pm.tracker, pm.dt_tracker)
    opt = EntryOptimizer()
    val = PreTradeValidator(eq)
    brk = IndependentCircuitBreakers()
    notif = Notifier()
    sig = SignalGenerator()
    flow = OptionsFlowScorer()
    trade_log = TradeLogger()
    slip = SlippageTracker()
    corr_sizer = CorrelationSizer()
    vol_fetch = VolumeFetcher(exe, schwab_client)
    spy_corr = SPYCorrelationMonitor()
    opt_sel = OptionsSelector()

    # Pre-market gap scan (#6)
    premarket = PremarketScanner()
    logger.info("Running pre-market gap scan...")
    gap_alerts = premarket.scan(pm.tracker, exe, notif)
    premarket.execute_gap_exits(
        gap_alerts, pm.tracker, exe, pm.dt_tracker
    )

    # Load watchlist
    wl = {}
    wp = "config/watchlist.json"
    if os.path.exists(wp):
        with open(wp) as f:
            wl = json.load(f)

    # Calculate entry zones
    for pick in wl.get("stocks", []):
        sym = pick["symbol"]
        df = sig.load_price_data(sym)
        if len(df) >= 20:
            z = opt.calculate_entry_zone(sym, df, pick)
            if z:
                opt.active_zones[sym] = z

    for pick in wl.get("leveraged_etfs", []):
        sym = pick["symbol"]
        df = sig.load_price_data(sym)
        if len(df) >= 20:
            z = opt.calculate_entry_zone(sym, df, pick)
            if z:
                opt.active_zones[sym] = z

    for pick in wl.get("options", []):
        sym = pick["symbol"]
        df = sig.load_price_data(sym)
        if len(df) >= 20:
            z = opt.calculate_entry_zone(sym, df, pick)
            if z:
                opt.active_zones[sym] = z

    az = len(opt.active_zones)
    logger.info(
        f"Monitor: {az} zones "
        f"({'PAPER' if paper else 'LIVE'})"
    )

    cnt = 0
    rp = {}
    spy_open_price = None

    while True:
        if not is_market_hours():
            now = datetime.now()
            if now.hour >= 16 and now.minute > 5:
                logger.info("Market closed.")
                # Log any remaining closed positions
                break
            time.sleep(60)
            continue

        cnt += 1
        syms = set()
        for p in pm.tracker.get_open().values():
            syms.add(p.symbol)
        for s in opt.active_zones:
            syms.add(s)
        syms.add("SPY")

        prices = {}
        for s in syms:
            try:
                q = exe.get_current_quote(s)
                p = q.get("last") or q.get("ask") or 0
                if p > 0:
                    prices[s] = p
                    if s not in rp:
                        rp[s] = []
                    rp[s].append(p)
                    if len(rp[s]) > 12:
                        rp[s] = rp[s][-12:]
            except Exception:
                pass

        if spy_open_price is None and "SPY" in prices:
            spy_open_price = prices["SPY"]

        # Update SPY correlation (#15)
        if "SPY" in prices:
            spy_corr.update(prices, prices["SPY"])

        # Calculate SPY change
        spy_change = 0
        if spy_open_price and "SPY" in prices:
            spy_change = (
                (prices["SPY"] - spy_open_price)
                / spy_open_price
            )

        # ── EXIT CHECKS ──────────────────────────
        exits = exit_eng.evaluate_all(prices, spy_change)
        for order in exits:
            if not order:
                continue

            # Smart SPY breaker (#15)
            if order.get("reason") == "spy_breaker":
                sym = order["symbol"]
                trigger, msg = (
                    spy_corr.should_trigger_spy_breaker(
                        sym, spy_change
                    )
                )
                if not trigger:
                    logger.info(
                        f"SPY breaker skipped {sym}: {msg}"
                    )
                    continue

            ok, r = val.validate(
                order, prices,
                pm.tracker.get_summary(prices)
            )
            if not ok:
                continue
            if order.get("is_day_trade"):
                lp = abs(
                    order.get("unrealized_pnl_pct", 0)
                )
                if not pm.dt_tracker.should_allow_emergency(
                    order["symbol"], lp
                ):
                    continue
                pm.dt_tracker.record(
                    order["symbol"], order["reason"]
                )
            result = exe.submit_order(
                order["symbol"], "SELL",
                order["quantity"],
                order.get("limit_price"),
            )
            if result.get("status") in (
                "SUBMITTED", "FILLED"
            ):
                # Track slippage (#8)
                if result.get("status") == "FILLED":
                    slip.record(
                        order["symbol"], "SELL",
                        order.get("limit_price", 0),
                        result.get(
                            "fill_price",
                            order.get("limit_price", 0),
                        ),
                        order["quantity"],
                    )
                key = order["position_key"]
                pos = pm.tracker.positions.get(key)
                if pos:
                    qty = order["quantity"]
                    if qty >= pos.current_quantity:
                        pm.tracker.close_position(
                            key,
                            order["limit_price"],
                            order["reason"],
                        )
                        # Log completed trade (#2)
                        trade_log.log_trade(pos.to_dict())
                    else:
                        pm.tracker.partial_close(
                            key, qty,
                            order["limit_price"],
                            order["reason"],
                        )

        # ── ENTRY CHECKS ─────────────────────────
        in_win, wtype = opt.is_in_entry_window()
        if in_win and not pm.halted:
            # Get existing symbols for correlation (#7)
            existing = [
                p.symbol
                for p in pm.tracker.get_open().values()
            ]

            for sym, zone in list(
                opt.active_zones.items()
            ):
                if zone.triggered:
                    continue
                price = prices.get(sym)
                if not price:
                    continue
                if opt.should_cancel(sym, price):
                    del opt.active_zones[sym]
                    continue

                prev = rp.get(sym, [])

                # Get real volume (#9)
                vd = vol_fetch.get_realtime_volume(sym)

                triggered, trigs = opt.check_triggers(
                    sym, price,
                    vd["current_volume"],
                    vd["avg_volume"],
                    vd["bid_size"],
                    vd["ask_size"],
                    prev,
                )

                if not triggered:
                    continue

                pick = zone.watchlist_data
                if not pick:
                    continue

                # Correlation sizing (#7)
                corr_mod = corr_sizer.get_size_modifier(
                    sym, existing
                )

                # STOCK entry
                if sym not in LEVERED and "shares" in pick:
                    atr = pick.get("atr", price * 0.02)
                    sizing = pm.get_size("STOCK", price, atr)
                    shares = sizing.get("shares", 0)
                    sz_mod = opt.get_position_size_modifier(
                        zone, trigs
                    )
                    shares = max(
                        1, int(shares * sz_mod * corr_mod)
                    )
                    cost = shares * price
                    sec = pick.get("sector", "")
                    can, reason = pm.can_enter(
                        "STOCK", sec, cost
                    )
                    if can:
                        lp = round(price * 1.002, 2)
                        r = exe.submit_order(
                            sym, "BUY", shares, lp
                        )
                        if r.get("status") in (
                            "SUBMITTED", "FILLED"
                        ):
                            if r.get("status") == "FILLED":
                                slip.record(
                                    sym, "BUY", lp,
                                    r.get("fill_price", lp),
                                    shares,
                                )
                            pos = Position(
                                symbol=sym,
                                instrument="STOCK",
                                direction="LONG",
                                entry_price=lp,
                                quantity=shares,
                                stop_loss=pick.get(
                                    "stop_loss", 0
                                ),
                                target_1=pick.get(
                                    "target_1", 0
                                ),
                                target_2=pick.get(
                                    "target_2", 0
                                ),
                                target_3=pick.get(
                                    "target_3", 0
                                ),
                                entry_date=(
                                    date.today().isoformat()
                                ),
                                signal_score=pick.get(
                                    "score", 0
                                ),
                                sector=sec,
                                max_hold_days=7,
                            )
                            pm.tracker.open_position(pos)
                            existing.append(sym)
                            notif.send_trade_alert(
                                "BUY STOCK", sym,
                                {"shares": shares,
                                 "price": lp,
                                 "triggers": trigs},
                            )
                            del opt.active_zones[sym]

                # ETF entry
                elif sym in LEVERED:
                    sizing = pm.get_size("ETF", price, 0)
                    shares = sizing.get("shares", 0)
                    sz_mod = opt.get_position_size_modifier(
                        zone, trigs
                    )
                    shares = max(
                        1, int(shares * sz_mod * corr_mod)
                    )
                    cost = shares * price
                    can, reason = pm.can_enter(
                        "ETF", "", cost
                    )
                    if can:
                        lp = round(price * 1.002, 2)
                        sp = round(price * 0.95, 2)
                        r = exe.submit_order(
                            sym, "BUY", shares, lp
                        )
                        if r.get("status") in (
                            "SUBMITTED", "FILLED"
                        ):
                            if r.get("status") == "FILLED":
                                slip.record(
                                    sym, "BUY", lp,
                                    r.get("fill_price", lp),
                                    shares,
                                )
                            pos = Position(
                                symbol=sym,
                                instrument="ETF",
                                direction="LONG",
                                entry_price=lp,
                                quantity=shares,
                                stop_loss=sp,
                                target_1=round(
                                    lp * 1.08, 2
                                ),
                                target_2=round(
                                    lp * 1.15, 2
                                ),
                                target_3=0,
                                entry_date=(
                                    date.today().isoformat()
                                ),
                                signal_score=pick.get(
                                    "score", 0
                                ),
                                sector="",
                                max_hold_days=5,
                            )
                            pm.tracker.open_position(pos)
                            existing.append(sym)
                            notif.send_trade_alert(
                                "BUY ETF", sym,
                                {"shares": shares,
                                 "price": lp,
                                 "triggers": trigs},
                            )
                            del opt.active_zones[sym]

                # OPTIONS entry (#13)
                elif "direction" in pick:
                    direction = pick["direction"]
                    mc = pick.get("max_cost", 400)
                    can, reason = pm.can_enter(
                        direction, "", mc
                    )
                    if can and schwab_client:
                        try:
                            import httpx
                            resp = schwab_client.get_option_chain(
                                sym, strike_count=20
                            )
                            if resp.status_code == httpx.codes.OK:
                                chain = resp.json()
                                contract = opt_sel.select_contract(
                                    chain, direction,
                                    mc, price,
                                )
                                if contract:
                                    notif.send_trade_alert(
                                        f"OPTIONS {direction}",
                                        sym,
                                        {"contract": contract[
                                            "description"
                                        ],
                                         "strike": contract[
                                             "strike"
                                         ],
                                         "dte": contract["dte"],
                                         "cost": contract[
                                             "total_cost"
                                         ],
                                         "triggers": trigs},
                                    )
                        except Exception as e:
                            logger.debug(f"Opt err: {e}")
                    del opt.active_zones[sym]

        # ── PERIODIC STATUS ──────────────────────
        if cnt % 10 == 0:
            upnl = sum(
                p.unrealized_pnl(
                    prices.get(p.symbol, p.entry_price)
                )
                for p in pm.tracker.get_open().values()
            )
            intra = upnl / eq if eq > 0 else 0
            brk.update_and_check(eq + upnl, intra)
            op = pm.tracker.get_open()
            az = len(opt.active_zones)
            logger.info(
                f"#{cnt} | {len(op)} pos | "
                f"{az} zones | {wtype} | "
                f"SPY {spy_change:+.2%}"
            )

        time.sleep(30)

    # End of day: log trade stats
    stats = trade_log.get_stats()
    if stats.get("total", 0) > 0:
        logger.info(
            f"Trade stats: {stats['total']} trades, "
            f"{stats.get('win_rate', 0):.0%} WR, "
            f"${stats.get('total_pnl', 0):+.2f}"
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        logger.warning("LIVE MODE!")
        c = input("Type YES: ")
        if c != "YES":
            exit()
    run_loop(paper=not args.live)
