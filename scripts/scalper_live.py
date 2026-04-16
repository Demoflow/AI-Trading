"""
0DTE Scalper v6.0 — Pattern & Sequence Aware.
- Long calls and puts only | 2% per trade | uncapped trades
- Settled cash tracking (T+1)
- Pattern engine: 4 candle-sequence fingerprints
- Regime transition detection (re-classifies every 30 min)
- GEX level interaction tracking (rejected vs absorbed)
- Time-of-day sequence gating (6 behavioral windows)
- Breadth divergence detection (accumulation / distribution)
- All contexts feed into signal_engine.scan() as confidence modifiers
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()

POLL_INTERVAL = 5
SCALP_EQUITY  = 25000

SYMBOLS = [
    # Broad market ETFs
    "SPY", "QQQ", "IWM",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    # High-volatility / high-flow
    "TSLA", "AMD", "NFLX", "PLTR", "MSTR",
    # Sector ETF
    "SMH",
    # Financials / Energy
    "COIN", "JPM", "XOM",
]


def get_option_value(client, sym):
    try:
        resp = client.get_quote(sym)
        if resp and resp.status_code == 200:
            data = resp.json()
            q    = data.get(sym, {}).get("quote", {})
            mark = q.get("mark", 0)
            bid  = q.get("bidPrice", 0)
            ask  = q.get("askPrice", 0)
            if mark > 0:
                return mark
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
        return None
    except Exception:
        return None


def _acquire_lock():
    """Write a PID lock file. Returns False if another instance is already running."""
    import psutil
    lock_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scalper.lock")
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                old_pid = int(f.read().strip())
            if psutil.pid_exists(old_pid):
                return False, lock_path, old_pid
        except Exception:
            pass  # Stale/corrupt lock — overwrite it
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    return True, lock_path, os.getpid()


def _release_lock(lock_path):
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass


def run():
    from utils.logging_setup import setup_logging
    setup_logging()

    # ── SINGLE-INSTANCE LOCK ──
    acquired, lock_path, pid = _acquire_lock()
    if not acquired:
        logger.warning("=" * 60)
        logger.warning(f"SCALPER ALREADY RUNNING (PID {pid}) — exiting.")
        logger.warning("Stop the existing instance before launching a new one.")
        logger.warning("=" * 60)
        return

    logger.info("=" * 60)
    logger.info("0DTE SCALPER v6.0 — PATTERN & SEQUENCE AWARE")
    logger.info(f"Starting equity: ${SCALP_EQUITY:,.2f}")
    logger.info("Long calls + puts | 2% per trade | uncapped | settled cash")
    logger.info("=" * 60)

    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.error(f"Schwab required: {e}")
        _release_lock(lock_path)
        return

    from scalper.realtime_data    import RealtimeDataEngine
    from scalper.signal_engine    import ScalperSignal
    from scalper.contract_picker  import ContractPicker
    from scalper.risk_manager     import ScalperRiskManager
    from scalper.executor         import ScalperExecutor
    from scalper.day_classifier   import DayClassifier
    from scalper.gex_intraday     import IntradayGEX
    from scalper.market_internals import MarketInternals
    from scalper.pattern_engine   import PatternEngine
    from scalper.time_context     import TimeContextFilter

    data_engine  = RealtimeDataEngine(client)
    signals      = ScalperSignal()
    picker       = ContractPicker(client)
    executor     = ScalperExecutor(SCALP_EQUITY)
    risk         = ScalperRiskManager(executor.portfolio.get("equity", SCALP_EQUITY))
    day_class    = DayClassifier()
    gex          = IntradayGEX(client)
    internals    = MarketInternals(client)
    pattern_eng  = PatternEngine()
    time_ctx     = TimeContextFilter()

    # ── STARTUP TASKS ──
    available = executor.advance_settlement()
    logger.info(f"Settled cash available: ${available:,.2f}")

    expired = executor.expire_stale_positions()
    if expired:
        logger.info(f"Cleaned up {expired} expired positions")

    risk.update_equity(executor.portfolio.get("equity", SCALP_EQUITY))

    # VIX
    vix = 20
    try:
        import httpx
        r = client.get_quote("$VIX")
        if r.status_code == httpx.codes.OK:
            vix = r.json().get("$VIX", {}).get("quote", {}).get("lastPrice", 20)
    except Exception:
        pass

    is_event, event = risk.is_event_day()
    if is_event:
        logger.warning(f"EVENT DAY: {event} — entry bar raised automatically")

    # Cycle tracking
    cycle         = 0
    classified    = False
    gap_captured  = False

    # Cached market context (refresh intervals in cycles, 1 cycle = 5s)
    gex_cache     = {}
    breadth_cache = None
    em_cache      = {}
    div_cache     = None
    last_gex      = 0
    last_breadth  = 0
    last_em       = 0
    last_regime   = 0   # Regime update: every 360 cycles (30 min)

    logger.info(f"VIX: {vix:.1f}")
    logger.info("Building candles (1m + 5m)...")
    data_engine.seed_history()

    import atexit
    atexit.register(_release_lock, lock_path)

    while True:
        now = datetime.now()
        h   = now.hour + now.minute / 60.0

        # ── SYNC POSITION COUNT AND EQUITY EACH CYCLE ──
        risk.open_positions = len(executor.get_open_positions())
        risk.update_equity(executor.portfolio.get("equity", SCALP_EQUITY))

        # ── FORCE-CLOSE ALL POSITIONS AT 3:45 PM ET (2:45 PM CT) ──
        if 14.75 <= h < 14.85 and cycle > 0:
            open_pos = executor.get_open_positions()
            if open_pos:
                logger.warning(f"3:45 PM FORCE CLOSE: {len(open_pos)} positions")
                for pos in open_pos:
                    csym = pos.get("contract", "")
                    if not csym:
                        continue
                    current = get_option_value(client, csym)
                    if current is not None:
                        result = executor.close_position(pos["id"], current)
                    else:
                        result = executor.close_position(pos["id"], 0.01)
                    if result["status"] == "CLOSED":
                        risk.record_trade(result["pnl"])
                        logger.info(
                            f"FORCE CLOSED: {pos['symbol']} "
                            f"P&L=${result['pnl']:+,.2f}"
                        )

        # ── SESSION BOUNDARY ──
        if now.weekday() >= 5 or h < 8.4 or h >= 15.1:
            if h >= 15.1 and cycle > 0:
                # Safety flush — close any positions still open at session end
                open_pos = executor.get_open_positions()
                if open_pos:
                    logger.warning(f"EOD safety close: {len(open_pos)} positions still open")
                    for pos in open_pos:
                        csym = pos.get("contract", "")
                        if not csym:
                            continue
                        current = get_option_value(client, csym)
                        result = executor.close_position(pos["id"], current if current else 0.01)
                        if result["status"] == "CLOSED":
                            risk.record_trade(result["pnl"])
                            logger.info(
                                f"EOD CLOSED: {pos['symbol']} "
                                f"P&L=${result['pnl']:+,.2f}"
                            )
                s = executor.get_summary()
                logger.info("=" * 60)
                logger.info("SCALPER v6.0 END OF DAY")
                logger.info(f"  Day: {day_class.day_type}")
                logger.info(
                    f"  Trades:{s['today_trades']} "
                    f"W:{s['today_wins']} L:{s['today_losses']} "
                    f"P&L:${s['today_pnl']:+,.2f}"
                )
                logger.info(
                    f"  Total:{s['total_trades']} "
                    f"WR:{s['win_rate']:.0%} "
                    f"Equity:${s['equity']:,.2f} "
                    f"P&L:${s['total_pnl']:+,.2f}"
                )
                logger.info("=" * 60)
                break
            if cycle % 12 == 0:
                logger.info(f"Waiting... ({now.strftime('%H:%M')})")
            cycle += 1
            time.sleep(POLL_INTERVAL)
            continue

        cycle += 1
        quotes = data_engine.poll()

        # ── INITIAL DAY CLASSIFICATION (after 30 min) ──
        if not classified:
            spy_b = data_engine.builders_5m.get("SPY")
            if spy_b and spy_b.candle_count() >= 6:
                spy_snap_tmp = data_engine.get_snapshot("SPY")
                atr_now = spy_snap_tmp["atr"] if spy_snap_tmp else None
                day_class.classify(spy_b, vix, atr=atr_now)
                classified = True
                internals.initialize()
                # Eagerly populate breadth so the direction filter is armed
                # before the first entry cycle. Without this, breadth_cache
                # stays None for ~2 min after any restart, bypassing the block
                # that prevents PUT entries on STRONG_BULLISH days (and vice versa).
                breadth_cache = internals.get_breadth()
                if breadth_cache:
                    spy_px_init = data_engine.get_snapshot("SPY")
                    spy_px_init = spy_px_init["price"] if spy_px_init else 0
                    internals.record_breadth(breadth_cache, spy_px_init)
                    div_cache = internals.get_divergence()
                    logger.info(
                        f"Breadth (startup): {breadth_cache['signal']} "
                        f"({breadth_cache['advancing']}/"
                        f"{len(internals._baseline)} up)"
                    )
                last_breadth = cycle
                risk.day_type = day_class.day_type
                logger.info(
                    f"Day classified: {day_class.day_type} "
                    f"(transition detection active)"
                )

        # ── GAP DIRECTION CAPTURE (first completed SPY candle) ──
        if not gap_captured:
            spy_b = data_engine.builders_5m.get("SPY")
            if spy_b and spy_b.candle_count() >= 1:
                first_candle = list(spy_b.candles)[0] if spy_b.candles else None
                time_ctx.capture_gap(first_candle)
                gap_captured = True

        # ── GEX UPDATE (every 5 min = 60 cycles) ──
        if cycle - last_gex >= 60:
            for sym in SYMBOLS:
                gex_cache[sym] = gex.analyze(sym)
            last_gex = cycle
            g = gex_cache.get("SPY")
            if g:
                risk.gex_regime = g["regime"]
                logger.info(
                    f"GEX: SPY {g['regime']} "
                    f"pin=${g['pin_level']} "
                    f"walls=${g['put_wall']}–${g['call_wall']}"
                )

        # ── BREADTH UPDATE (every 2 min = 24 cycles) ──
        if cycle - last_breadth >= 24:
            _fresh_breadth = internals.get_breadth()
            if _fresh_breadth is not None:
                breadth_cache = _fresh_breadth  # Never overwrite good data with None
            # Feed breadth history for divergence tracking
            if breadth_cache:
                spy_snap = data_engine.get_snapshot("SPY")
                spy_px   = spy_snap["price"] if spy_snap else 0
                internals.record_breadth(breadth_cache, spy_px)
                div_cache = internals.get_divergence()
                if div_cache and div_cache["type"] not in ("NEUTRAL",):
                    logger.info(
                        f"Breadth: {breadth_cache['signal']} | "
                        f"Divergence: {div_cache['type']} "
                        f"score={div_cache['score']:+d} → {div_cache['signal_bias']}"
                    )
                else:
                    logger.info(
                        f"Breadth: {breadth_cache['signal']} "
                        f"({breadth_cache['advancing']}/"
                        f"{len(internals._baseline)} up)"
                    )
            last_breadth = cycle

        # ── EXPECTED MOVE UPDATE (every 10 min = 120 cycles) ──
        if cycle - last_em >= 120:
            for sym in SYMBOLS:
                em_cache[sym] = picker.get_expected_move(sym)
            last_em = cycle
            em = em_cache.get("SPY")
            if em:
                logger.info(
                    f"Expected Move: SPY +/-${em['expected_move']} "
                    f"({em['expected_move_pct']:.2f}%) "
                    f"${em['lower_bound']}–${em['upper_bound']}"
                )

        # ── REGIME UPDATE (every 30 min = 360 cycles) ──
        if classified and cycle - last_regime >= 360:
            spy_b = data_engine.builders_5m.get("SPY")
            spy_snap = data_engine.get_snapshot("SPY")
            atr_now  = spy_snap["atr"] if spy_snap else None
            day_class.update_regime(spy_b, vix, atr=atr_now)
            risk.day_type = day_class.day_type
            last_regime = cycle
            transition = day_class.get_regime_transition()
            if transition:
                implication = day_class.get_transition_implication()
                logger.warning(
                    f"REGIME TRANSITION: {transition} → {implication}"
                )

        # ── GEX LEVEL INTERACTION TRACKING (every 30s = 6 cycles) ──
        if cycle % 6 == 0:
            spy_snap = data_engine.get_snapshot("SPY")
            if spy_snap and gex_cache.get("SPY"):
                gex.record_price_interaction("SPY", spy_snap["price"], gex_cache["SPY"])
            qqq_snap = data_engine.get_snapshot("QQQ")
            if qqq_snap and gex_cache.get("QQQ"):
                gex.record_price_interaction("QQQ", qqq_snap["price"], gex_cache["QQQ"])

        # ── TIME CONTEXT ──
        t_ctx = time_ctx.get_context(h)

        # ── STRATEGY SELECTION ──
        if classified:
            allowed = day_class.get_strategy_for_window(h)
        else:
            allowed = ["VWAP_PULLBACK", "EMA_MOMENTUM"]

        # Always include core directional strategies once market is open
        if h >= 8.58 and t_ctx.get("entry_allowed", True):
            for s in ["VWAP_PULLBACK", "EMA_MOMENTUM", "MOMENTUM_FADE"]:
                if s not in allowed:
                    allowed.append(s)

        # Add breakout and aggressive directional on trending/volatile days
        if day_class.day_type in ("TRENDING", "VOLATILE"):
            for s in ["DIRECTIONAL_BUY", "ORB_BREAKOUT"]:
                if s not in allowed:
                    allowed.append(s)

        # ── CHECK EXITS ──
        for pos in executor.get_open_positions():
            csym = pos.get("contract", "")
            if not csym:
                continue
            current = get_option_value(client, csym)
            if current is None:
                continue
            current_value = current * pos["qty"] * 100

            if current_value > pos.get("peak_value", 0):
                pos["peak_value"] = current_value

            should_exit, reason, action = risk.check_exit(pos, current_value)
            if should_exit:
                result = executor.close_position(pos["id"], current)
                if result["status"] == "CLOSED":
                    risk.record_trade(result["pnl"])
                    logger.info(
                        f"EXIT [{reason}]: {pos['direction']} {pos['symbol']} "
                        f"P&L=${result['pnl']:+,.2f}"
                    )

        # ── CHECK ENTRY SIGNALS ──
        can_trade, trade_reason = risk.can_trade()

        # Hard time gates (belt + suspenders over time_context)
        if h >= 15.5 or h < 8.58:
            can_trade = False

        # Time context entry gate (window-level)
        if not t_ctx.get("entry_allowed", True):
            can_trade = False

        if can_trade and allowed:
            for sym in SYMBOLS:
                snap_5m = data_engine.get_snapshot(sym)
                snap_1m = data_engine.get_entry_snapshot(sym)
                if not snap_5m:
                    continue

                # ── PATTERN CONTEXT (per symbol, per cycle) ──
                candles_5m = data_engine.builders_5m[sym].get_all_candles()
                pat_ctx = pattern_eng.analyze(
                    candles_5m,
                    vwap=snap_5m.get("vwap", 0),
                    atr=snap_5m.get("atr", 1),
                    expected_move=em_cache.get(sym),
                )

                # ── GEX WALL CONTEXT (per symbol) ──
                gex_wall_ctx = gex.get_wall_context(
                    sym,
                    snap_5m["price"],
                    gex_cache.get(sym),
                ) if gex_cache.get(sym) else None

                new_signals = signals.scan(
                    snapshot_5m=snap_5m,
                    snapshot_1m=snap_1m,
                    allowed_strategies=allowed,
                    gex_profile=gex_cache.get(sym),
                    breadth=breadth_cache,
                    expected_move=em_cache.get(sym),
                    pattern_context=pat_ctx,
                    time_context=t_ctx,
                    divergence=div_cache,
                    gex_wall_context=gex_wall_ctx,
                )

                open_syms = {p["symbol"] for p in executor.get_open_positions()}

                # ── QUIET-DAY CONFIDENCE FLOOR ──
                # On choppy/quiet days the market doesn't move enough to
                # justify borderline setups — theta kills them before they work.
                min_conf = risk.get_min_confidence(day_class.day_type if classified else "")

                for signal in new_signals:
                    can_still, _ = risk.can_trade()
                    if not can_still:
                        break
                    if h >= 15.5 or h < 8.58:
                        break

                    if signal["symbol"] in open_syms:
                        logger.debug(
                            f"  Blocked {signal['type']} {signal['symbol']}: "
                            f"open position exists"
                        )
                        continue

                    # ── QUIET-DAY CONFIDENCE FILTER ──
                    if signal["confidence"] < min_conf:
                        logger.debug(
                            f"  Blocked {signal['symbol']}: conf {signal['confidence']} "
                            f"< {min_conf} ({day_class.day_type if classified else 'unclassified'} floor)"
                        )
                        continue

                    # ── DIRECTIONAL CONCENTRATION LIMIT (max 2 same direction) ──
                    open_positions = executor.get_open_positions()
                    same_dir = sum(1 for p in open_positions if p["direction"] == signal["direction"])
                    if same_dir >= 2:
                        logger.info(
                            f"  Blocked {signal['symbol']}: "
                            f"{same_dir} {signal['direction']}s already open (max 2)"
                        )
                        continue

                    # ── SPY MACRO RSI GATE ──
                    # Avoid adding directional bets when SPY is already stretched.
                    # Exception: NEGATIVE GEX days where momentum continuation is valid.
                    spy_snap_rsi = data_engine.get_snapshot("SPY")
                    spy_rsi = spy_snap_rsi["rsi"] if spy_snap_rsi else 50
                    spy_gex = gex_cache.get("SPY", {})
                    gex_negative = spy_gex.get("regime") == "NEGATIVE"
                    if not gex_negative:
                        if signal["direction"] == "PUT" and spy_rsi < 40:
                            logger.info(
                                f"  Blocked {signal['symbol']}: SPY RSI {spy_rsi:.0f} "
                                f"oversold — no new PUTs"
                            )
                            continue
                        if signal["direction"] == "CALL" and spy_rsi > 65:
                            logger.info(
                                f"  Blocked {signal['symbol']}: SPY RSI {spy_rsi:.0f} "
                                f"overbought — no new CALLs"
                            )
                            continue

                    # Theta acceleration gate
                    ok, tf_reason = picker.should_allow_buy(signal["confidence"])
                    if not ok:
                        logger.info(f"  Blocked {signal['symbol']}: {tf_reason}")
                        continue

                    # Breadth direction confirmation
                    if breadth_cache:
                        b_ok, b_reason = internals.confirms_direction(
                            signal["direction"], breadth_cache
                        )
                        if not b_ok:
                            logger.info(f"  Blocked {signal['symbol']}: {b_reason}")
                            continue

                    max_cost = risk.get_position_size(signal["confidence"])

                    if executor.get_available_cash() < max_cost:
                        logger.info(
                            f"  Blocked {signal['symbol']}: insufficient settled cash "
                            f"(${executor.get_available_cash():,.2f} < ${max_cost:,.2f})"
                        )
                        continue

                    rr_str = f" RR:{signal.get('rr_ratio','?')}:1"
                    logger.info(
                        f"SIGNAL: {signal['type']} {signal['direction']} {signal['symbol']} "
                        f"conf:{signal['confidence']}{rr_str} "
                        f"[{t_ctx['window']}] | {signal['reason']}"
                    )

                    contract = picker.pick(
                        sym, signal["direction"], max_cost, "LONG_OPTION",
                        confidence=signal["confidence"]
                    )
                    if contract:
                        result = executor.open_position(signal, contract, max_cost)
                        if result["status"] == "FILLED":
                            risk.open_positions += 1
                            open_syms.add(signal["symbol"])
                            risk.update_equity(
                                executor.portfolio.get("equity", SCALP_EQUITY)
                            )
                            # Clear acted-on transition after first new trade
                            if day_class.get_regime_transition():
                                day_class.clear_transition()
                    else:
                        logger.info(f"  No contract: {sym} {signal['direction']}")

        # ── STATUS LOG (every 60s = 12 cycles) ──
        if cycle % 12 == 0:
            s = executor.get_summary()
            spy = data_engine.get_snapshot("SPY")
            spy_str = ""
            if spy:
                spy_str = (
                    f"SPY:${spy['price']} "
                    f"{spy['ema_trend']} "
                    f"RSI:{spy['rsi']:.0f} "
                    f"VWAP:{spy['vwap_pct']:+.2f}% "
                    f"{spy['vwap_band']}"
                )
            gex_str = ""
            g = gex_cache.get("SPY")
            if g:
                gex_str = f"GEX:{g['regime']}"
            day_str   = day_class.day_type if classified else "BUILDING"
            window_str = t_ctx.get("window", "?")
            div_str    = ""
            if div_cache and div_cache["type"] != "NEUTRAL":
                div_str = f" DIV:{div_cache['type'][:4]}"

            logger.info(
                f"[{now.strftime('%H:%M:%S')}] {spy_str} | "
                f"{gex_str} Day:{day_str} Win:{window_str}{div_str} | "
                f"Open:{s['open_positions']} "
                f"T:{s['today_trades']} "
                f"P&L:${s['today_pnl']:+,.0f} "
                f"Eq:${s['equity']:,.0f} "
                f"Settled:${s['settled_cash']:,.0f}"
            )

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
