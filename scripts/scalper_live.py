"""
VWAP Stock Scalper v7.0 — Autonomous Intraday Trading System.

Replaces the 0DTE options scalper with a VWAP-based stock scalping system.
Targets aggressive growth on a $25,000 account.

Entry point: python scripts/scalper_live.py
Launched via SCALPER.bat or Windows Task Scheduler.

Core loop runs every 30 seconds during market hours.
All times in CT (America/Chicago).
"""

import os
import sys
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _now_ct():
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()


def _hour_ct() -> float:
    n = _now_ct()
    return n.hour + n.minute / 60.0 + n.second / 3600.0


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from loguru import logger
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(_BASE_DIR) / ".env")  # Explicit path — works from Task Scheduler

POLL_INTERVAL = 30      # 30-second main loop cycle
SCALP_EQUITY = 25000

import json as _json

_SCALPER_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "agent_overrides.json"
)
_scalper_ov_mtime: float = 0.0
_scalper_ov_cache: dict = {}


def _load_scalper_overrides() -> dict:
    global _scalper_ov_mtime, _scalper_ov_cache
    try:
        mtime = os.path.getmtime(_SCALPER_OVERRIDES_PATH)
        if mtime != _scalper_ov_mtime:
            with open(_SCALPER_OVERRIDES_PATH, "r") as f:
                _scalper_ov_cache = _json.load(f)
            _scalper_ov_mtime = mtime
    except Exception:
        pass
    return _scalper_ov_cache


def _save_scalper_overrides(data: dict):
    try:
        with open(_SCALPER_OVERRIDES_PATH, "w") as f:
            _json.dump(data, f, indent=2)
    except Exception:
        pass


def _acquire_lock():
    """Write a PID lock file. Returns False if another instance is already running."""
    import psutil
    lock_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scalper.lock"
    )
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                old_pid = int(f.read().strip())
            if psutil.pid_exists(old_pid):
                return False, lock_path, old_pid
        except Exception:
            pass
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    return True, lock_path, os.getpid()


def _release_lock(lock_path):
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass


def get_stock_quote(client, symbol):
    """Get current stock price from Schwab."""
    try:
        import httpx
        resp = client.get_quote(symbol)
        if resp and resp.status_code == httpx.codes.OK:
            data = resp.json()
            q = data.get(symbol, {}).get("quote", {})
            price = q.get("lastPrice", 0)
            volume = q.get("totalVolume", 0)
            return price, volume
        return None, None
    except Exception:
        return None, None


def run():
    from utils.logging_setup import setup_logging
    setup_logging()

    # ── SINGLE-INSTANCE LOCK ──
    acquired, lock_path, pid = _acquire_lock()
    if not acquired:
        logger.warning("=" * 60)
        logger.warning(f"SCALPER ALREADY RUNNING (PID {pid}) — exiting.")
        logger.warning("=" * 60)
        return

    logger.info("=" * 60)
    logger.info("VWAP STOCK SCALPER v7.0")
    logger.info(f"Starting equity: ${SCALP_EQUITY:,.2f}")
    logger.info("Stock scalping | VWAP signals | 30s cycle")
    logger.info("=" * 60)

    # ── SCHWAB AUTH ──
    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.error(f"Schwab required: {e}")
        _release_lock(lock_path)
        return

    # ── INITIALIZE COMPONENTS ──
    from scalper.realtime_data    import RealtimeDataEngine
    from scalper.vwap_engine      import VWAPEngine
    from scalper.stock_universe   import StockUniverse
    from scalper.signal_engine    import ScalperSignal
    from scalper.risk_manager     import ScalperRiskManager
    from scalper.executor         import ScalperExecutor
    from scalper.exit_manager     import ExitManager
    from scalper.day_classifier   import DayClassifier
    from scalper.gex_intraday     import IntradayGEX
    from scalper.market_internals import MarketInternals

    stock_universe = StockUniverse()
    vwap_engine    = VWAPEngine()
    data_engine    = RealtimeDataEngine(client)
    signals        = ScalperSignal()
    executor       = ScalperExecutor(SCALP_EQUITY)
    risk           = ScalperRiskManager(executor.portfolio.get("equity", SCALP_EQUITY))
    exit_mgr       = ExitManager()
    day_class      = DayClassifier()
    gex            = IntradayGEX(client)
    internals      = MarketInternals(client)

    # ── STARTUP TASKS ──
    risk.update_equity(executor.portfolio.get("equity", SCALP_EQUITY))

    # Reconcile risk state from executor history
    from datetime import date as _date
    _today = _date.today().isoformat()
    _ds = executor.portfolio.get("daily_stats", {}).get(_today, {})
    if _ds.get("trades", 0) > risk.trades_today:
        risk.trades_today = _ds["trades"]
        risk.daily_pnl = _ds.get("pnl", 0.0)
        _hist = executor.portfolio.get("history", [])
        _today_hist = [t for t in _hist if t.get("entry_time", "")[:10] == _today]
        _streak = 0
        for _t in reversed(_today_hist):
            if _t.get("pnl", 0) < 0:
                _streak += 1
            else:
                break
        risk._consecutive_losses = max(risk._consecutive_losses, _streak)
        logger.info(
            f"Risk state reconciled: trades={risk.trades_today} "
            f"pnl=${risk.daily_pnl:+,.2f} streak={risk._consecutive_losses}"
        )

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
        logger.warning(f"EVENT DAY: {event}")

    # Cycle tracking
    cycle = 0
    classified = False
    _force_closed = False

    # Cached context (refresh intervals)
    gex_cache = {}
    breadth_cache = None
    last_gex = 0
    last_breadth = 0
    last_regime = 0
    last_vix = 0

    logger.info(f"VIX: {vix:.1f}")
    logger.info(f"Universe: {', '.join(stock_universe.get_all_symbols())}")
    logger.info("Seeding candle history...")
    data_engine.seed_history()

    # Seed VWAP engine from historical candles
    for sym in stock_universe.get_all_tracked_symbols():
        builder = data_engine.builders_5m.get(sym)
        if builder:
            candles = builder.get_all_candles()
            if candles:
                vwap_engine.seed_candles(sym, candles)

    logger.info("VWAP engine seeded from historical candles")

    # Track volume already fed to VWAP engine per symbol to avoid double-counting
    _vwap_fed_volume = {sym: 0 for sym in stock_universe.get_all_tracked_symbols()}
    _vwap_fed_block = {sym: None for sym in stock_universe.get_all_tracked_symbols()}

    import atexit
    atexit.register(_release_lock, lock_path)

    while True:
        now = _now_ct()
        h = _hour_ct()

        # ── AGENT OVERRIDE CHECK ──
        _ov = _load_scalper_overrides()
        _scalper_paused = _ov.get("scalper_paused", False)
        _blocked_scalper = set(s.upper() for s in _ov.get("blocked_symbols", []))
        if _ov.get("flatten_all"):
            logger.warning("[Agent] FLATTEN ALL — closing all positions")
            for pos in executor.get_open_positions():
                price, _ = get_stock_quote(client, pos["symbol"])
                if price:
                    result = executor.close_position(pos["id"], price, "agent_flatten")
                    if result["status"] == "CLOSED":
                        risk.record_trade(result["pnl"])
                        signals.record_exit(pos["symbol"])
            _ov["flatten_all"] = False
            _save_scalper_overrides(_ov)

        # ── SYNC STATE ──
        risk.open_positions = len(executor.get_open_positions())
        risk.update_equity(executor.portfolio.get("equity", SCALP_EQUITY))

        # ── FORCE-CLOSE ALL AT 3:30 PM CT ──
        if h >= 15.5 and not _force_closed and cycle > 0:
            open_pos = executor.get_open_positions()
            if open_pos:
                logger.warning(f"3:30 PM CT FORCE CLOSE: {len(open_pos)} positions")
                for pos in open_pos:
                    try:
                        price, _ = get_stock_quote(client, pos["symbol"])
                        if not price:
                            # Retry once after short delay
                            time.sleep(1)
                            price, _ = get_stock_quote(client, pos["symbol"])
                        if not price:
                            # Use last known price as fallback for EOD close
                            price = pos.get("current_price", pos.get("entry_price", 0))
                            logger.warning(f"EOD close using last known price for {pos['symbol']}: ${price}")
                        if not price:
                            logger.error(f"Cannot close {pos['symbol']} — no price available")
                            continue
                        result = executor.close_position(pos["id"], price, "EOD_FLATTEN")
                    except Exception as e:
                        logger.error(f"Force-close error {pos['symbol']}: {e}")
                        continue
                    if result["status"] == "CLOSED":
                        risk.record_trade(result["pnl"])
                        signals.record_exit(pos["symbol"])
            _force_closed = True

        # ── SESSION BOUNDARY ──
        if h < 8.4:
            _force_closed = False
        if now.weekday() >= 5 or h < 8.4 or h >= 16.0:
            if h >= 16.0 and cycle > 0:
                # Safety flush
                open_pos = executor.get_open_positions()
                if open_pos:
                    logger.warning(f"EOD safety close: {len(open_pos)} positions")
                    for pos in open_pos:
                        price, _ = get_stock_quote(client, pos["symbol"])
                        if not price:
                            price = pos.get("current_price", pos.get("entry_price", 0))
                        if price:
                            result = executor.close_position(pos["id"], price, "EOD_SAFETY")
                            if result["status"] == "CLOSED":
                                risk.record_trade(result["pnl"])
                s = executor.get_summary()
                logger.info("=" * 60)
                logger.info("VWAP SCALPER v7.0 END OF DAY")
                logger.info(f"  Day: {day_class.day_type}")
                logger.info(
                    f"  Trades:{s['today_trades']} "
                    f"W:{s['today_wins']} L:{s['today_losses']} "
                    f"P&L:${s['today_pnl']:+,.2f}"
                )
                logger.info(
                    f"  Total:{s['total_trades']} "
                    f"WR:{s['win_rate']:.0%} "
                    f"Equity:${s['equity']:,.2f}"
                )
                logger.info("=" * 60)
                break
            if cycle % 12 == 0:
                logger.info(f"Waiting... ({now.strftime('%H:%M')})")
            cycle += 1
            time.sleep(POLL_INTERVAL)
            continue

        cycle += 1

        # ── POLL QUOTES ──
        try:
            quotes = data_engine.poll()
        except Exception as e:
            logger.warning(f"Poll error: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        # ── UPDATE VWAP ENGINE ──
        for sym in stock_universe.get_all_tracked_symbols():
            builder = data_engine.builders_5m.get(sym)
            if builder:
                # Feed completed candles that haven't been fed yet
                cur = builder.get_current()
                cur_block = builder.get_current_block()
                prev_block = _vwap_fed_block.get(sym)
                if prev_block is not None and cur_block != prev_block:
                    # A new candle started — the previous candle was just completed
                    # and appended to builder.candles. Feed it to VWAP engine.
                    all_candles = list(builder.candles)
                    if all_candles:
                        last_completed = all_candles[-1]
                        vwap_engine.update_candle(sym, last_completed)
                    _vwap_fed_volume[sym] = 0
                _vwap_fed_block[sym] = cur_block
                # For the current (still-building) candle, feed only the volume delta
                if cur and cur.get("volume", 0) > 0:
                    fed = _vwap_fed_volume.get(sym, 0)
                    delta_vol = cur["volume"] - fed
                    if delta_vol > 0:
                        tp = (cur["high"] + cur["low"] + cur["close"]) / 3.0
                        vwap_engine.update(sym, tp, delta_vol)
                        _vwap_fed_volume[sym] = cur["volume"]

        # ── INITIAL DAY CLASSIFICATION (after 30 min) ──
        if not classified:
            spy_b = data_engine.builders_5m.get("SPY")
            if spy_b and spy_b.candle_count() >= 6:
                spy_snap = data_engine.get_snapshot("SPY")
                atr_now = spy_snap["atr"] if spy_snap else None
                day_class.classify(spy_b, vix, atr=atr_now)
                classified = True
                internals.initialize()
                breadth_cache = internals.get_breadth()
                if breadth_cache:
                    spy_px = spy_snap["price"] if spy_snap else 0
                    internals.record_breadth(breadth_cache, spy_px)
                last_breadth = cycle
                risk.day_type = day_class.day_type
                logger.info(f"Day classified: {day_class.day_type}")

        # ── GEX UPDATE (every 5 min = 10 cycles at 30s) ──
        if cycle - last_gex >= 10:
            for sym in stock_universe.get_all_symbols():
                try:
                    gex_cache[sym] = gex.analyze(sym)
                except Exception:
                    pass
            last_gex = cycle
            g = gex_cache.get("SPY")
            if g:
                risk.gex_regime = g["regime"]

        # ── BREADTH UPDATE (every 2 min = 4 cycles) ──
        if cycle - last_breadth >= 4:
            _fresh = internals.get_breadth()
            if _fresh is not None:
                breadth_cache = _fresh
            if breadth_cache:
                spy_snap = data_engine.get_snapshot("SPY")
                spy_px = spy_snap["price"] if spy_snap else 0
                internals.record_breadth(breadth_cache, spy_px)
            last_breadth = cycle

        # ── VIX UPDATE (every 5 min) ──
        if cycle - last_vix >= 10:
            try:
                import httpx
                r = client.get_quote("$VIX")
                if r.status_code == httpx.codes.OK:
                    vix = r.json().get("$VIX", {}).get("quote", {}).get("lastPrice", vix)
            except Exception:
                pass
            last_vix = cycle

        # ── REGIME UPDATE (every 15 min = 30 cycles) ──
        if classified and cycle - last_regime >= 30:
            spy_b = data_engine.builders_5m.get("SPY")
            spy_snap = data_engine.get_snapshot("SPY")
            atr_now = spy_snap["atr"] if spy_snap else None
            day_class.update_regime(spy_b, vix, atr=atr_now)
            risk.day_type = day_class.day_type
            last_regime = cycle

        # ── CHECK EXITS FOR ALL OPEN POSITIONS ──
        for pos in executor.get_open_positions():
            try:
                price, _ = get_stock_quote(client, pos["symbol"])
                if price is None:
                    continue

                # Update position with current price
                executor.update_position(pos["id"], price)

                # Check exit conditions
                should_exit, reason, action = exit_mgr.check_exit(
                    pos, price, vwap_engine
                )

                if should_exit:
                    if action == "HALF_EXIT":
                        shares_to_sell = pos["shares"] // 2
                        if shares_to_sell >= 1:
                            result = executor.partial_exit(
                                pos["id"], shares_to_sell, price, reason
                            )
                            if result and result["status"] == "PARTIAL":
                                logger.info(
                                    f"HALF EXIT [{reason}]: {pos['symbol']} "
                                    f"{shares_to_sell} shares P&L=${result['pnl']:+,.2f}"
                                )
                    else:  # FULL_EXIT
                        result = executor.close_position(pos["id"], price, reason)
                        if result["status"] == "CLOSED":
                            risk.record_trade(result["pnl"])
                            signals.record_exit(pos["symbol"])
                            logger.info(
                                f"EXIT [{reason}]: {pos['symbol']} "
                                f"P&L=${result['pnl']:+,.2f}"
                            )
            except Exception as e:
                logger.warning(f"Exit check error {pos['symbol']}: {e}")

        # ── CHECK ENTRY SIGNALS ──
        can_trade, trade_reason = risk.can_trade()

        if _scalper_paused:
            can_trade = False

        # Hard time gates
        if h >= 15.5 or h < 9.0:
            can_trade = False

        # Pre-classification position cap
        if not classified and risk.open_positions >= 2:
            can_trade = False

        if can_trade:
            open_syms = {p["symbol"] for p in executor.get_open_positions()}

            signal = signals.scan(
                vwap_engine=vwap_engine,
                data_engine=data_engine,
                stock_universe=stock_universe,
                day_type=day_class.day_type if classified else "",
                gex_regime=risk.gex_regime,
                vix_level=vix,
                breadth=breadth_cache,
                open_symbols=open_syms,
            )

            if signal:
                # Check minimum confidence (risk manager + universe)
                sym = signal["symbol"]
                if sym in _blocked_scalper:
                    signal = None

            if signal:
                sym = signal["symbol"]
                min_conf_risk = risk.get_min_confidence(
                    day_class.day_type if classified else ""
                )
                min_conf_univ = stock_universe.get_min_confidence(sym)
                min_conf = max(min_conf_risk, min_conf_univ)

                if signal["confidence"] >= min_conf:
                    # Calculate position size
                    position_limit = stock_universe.get_position_limit(
                        sym, risk.equity
                    )
                    share_count, dollar_notional = risk.get_position_size(
                        sym,
                        signal["entry_price"],
                        signal["stop_price"],
                        signal["confidence"],
                        position_limit=position_limit,
                    )

                    if share_count > 0:
                        result = executor.open_position(signal, share_count)
                        if result["status"] == "FILLED":
                            risk.open_positions += 1
                            risk.update_equity(
                                executor.portfolio.get("equity", SCALP_EQUITY)
                            )
                    else:
                        logger.debug(
                            f"Position size zero for {sym} "
                            f"(conf={signal['confidence']})"
                        )
                else:
                    logger.debug(
                        f"Signal below min conf: {sym} "
                        f"{signal['confidence']} < {min_conf}"
                    )

        # ── SAVE STATE ──
        executor.save_state()

        # ── STATUS LOG (every 60s = 2 cycles) ──
        if cycle % 2 == 0:
            s = executor.get_summary()
            spy_snap = data_engine.get_snapshot("SPY")
            spy_str = ""
            if spy_snap:
                spy_vwap = vwap_engine.get_vwap("SPY")
                spy_touch = vwap_engine.get_touch_count("SPY")
                spy_str = (
                    f"SPY:${spy_snap['price']:.2f} "
                    f"VWAP:${spy_vwap:.2f} "
                    f"T:{spy_touch}"
                )
            day_str = day_class.day_type if classified else "BUILDING"
            gex_str = f"GEX:{risk.gex_regime}" if risk.gex_regime else ""

            logger.info(
                f"[{now.strftime('%H:%M:%S')}] {spy_str} | "
                f"{gex_str} Day:{day_str} VIX:{vix:.1f} | "
                f"Open:{s['open_positions']} "
                f"T:{s['today_trades']} "
                f"P&L:${s['today_pnl']:+,.0f} "
                f"Eq:${s['equity']:,.0f}"
            )

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
