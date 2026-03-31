"""
0DTE Scalper v3.0 - Quant Rebuild.
- Premium selling first (iron condor default)
- Dual timeframe (1m entries, 5m bias)
- 3:1 R:R minimum on directional buys
- Expected move as #1 filter
- Max 8 trades/day
- GEX + breadth hard blocks
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
SCALP_EQUITY = 25000


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
        return None
    except Exception:
        return None


def run():
    from utils.logging_setup import setup_logging
    setup_logging()

    logger.info("=" * 60)
    logger.info("0DTE SCALPER v4.0 - LEVEL 3 PREMIUM MACHINE")
    logger.info(f"Equity: ${SCALP_EQUITY:,.2f}")
    logger.info("Philosophy: Sell premium by default. Level 3 enabled.")
    logger.info("Naked puts/calls, straddles, strangles, ratio spreads active.")
    logger.info("=" * 60)

    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.error(f"Schwab required: {e}")
        return

    from scalper.realtime_data import RealtimeDataEngine
    from scalper.signal_engine import ScalperSignal
    from scalper.contract_picker import ContractPicker
    from scalper.risk_manager import ScalperRiskManager
    from scalper.executor import ScalperExecutor
    from scalper.day_classifier import DayClassifier
    from scalper.gex_intraday import IntradayGEX
    from scalper.market_internals import MarketInternals

    data_engine = RealtimeDataEngine(client)
    signals = ScalperSignal()
    picker = ContractPicker(client)
    risk = ScalperRiskManager(SCALP_EQUITY)
    executor = ScalperExecutor(SCALP_EQUITY)
    day_class = DayClassifier()
    gex = IntradayGEX(client)
    internals = MarketInternals(client)

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

    cycle = 0
    classified = False
    gex_cache = {}
    breadth_cache = None
    em_cache = {}
    last_gex = 0
    last_breadth = 0
    last_em = 0

    logger.info(f"VIX: {vix:.1f}")
    logger.info("Building candles (1m + 5m)...")

    while True:
        now = datetime.now()
        h = now.hour + now.minute / 60.0

        if now.weekday() >= 5 or h < 8.4 or h >= 15.1:
            if h >= 15.1 and cycle > 0:
                s = executor.get_summary()
                logger.info("=" * 60)
                logger.info("SCALPER v3.0 END OF DAY")
                logger.info(f"  Day: {day_class.day_type}")
                logger.info(
                    f"  Trades:{s['today_trades']} "
                    f"W:{s['today_wins']} L:{s['today_losses']} "
                    f"P&L:${s['today_pnl']:+,.2f}"
                )
                logger.info(
                    f"  Total:{s['total_trades']} "
                    f"WR:{s['win_rate']:.0%} "
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

        # Classify day after 30 min of data
        if not classified:
            spy_b = data_engine.builders_5m.get("SPY")
            if spy_b and spy_b.candle_count() >= 6:
                day_class.classify(spy_b, vix)
                classified = True
                internals.initialize()
                logger.info(f"Day classified: {day_class.day_type}")

        # Update GEX every 5 min (60 cycles * 5s = 300s)
        if cycle - last_gex >= 60:
            for sym in ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD", "NFLX", "COIN", "BA", "JPM", "XOM"]:
                gex_cache[sym] = gex.analyze(sym)
            last_gex = cycle
            g = gex_cache.get("SPY")
            if g:
                logger.info(
                    f"GEX: SPY {g['regime']} "
                    f"pin=${g['pin_level']} "
                    f"walls=${g['put_wall']}-${g['call_wall']}"
                )

        # Update breadth every 2 min (24 cycles)
        if cycle - last_breadth >= 24:
            breadth_cache = internals.get_breadth()
            last_breadth = cycle
            if breadth_cache:
                logger.info(
                    f"Breadth: {breadth_cache['signal']} "
                    f"({breadth_cache['advancing']}/"
                    f"{len(internals._baseline)} up)"
                )

        # Update expected move every 10 min (120 cycles)
        if cycle - last_em >= 120:
            for sym in ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD", "NFLX", "COIN", "BA", "JPM", "XOM"]:
                em_cache[sym] = picker.get_expected_move(sym)
            last_em = cycle
            em = em_cache.get("SPY")
            if em:
                logger.info(
                    f"Expected Move: SPY +/-${em['expected_move']} "
                    f"({em['expected_move_pct']:.2f}%) "
                    f"range ${em['lower_bound']}-${em['upper_bound']}"
                )

        # â”€â”€ STRATEGY SELECTION â”€â”€
        # Default: premium selling strategies
        # Add directional only on trending days with strong signals
        allowed = []

        if classified:
            allowed = day_class.get_strategy_for_window(h)
        else:
            # Before classification, only allow safest strategies
            allowed = ["IRON_CONDOR", "CREDIT_SPREAD"]

        # Always add premium strategies in afternoon
        if h >= 13.0:
            for s in ["PREMIUM_SELL", "EOD_PIN"]:
                if s not in allowed:
                    allowed.append(s)

        # Only add directional on trending days
        if day_class.day_type in ("TRENDING", "VOLATILE"):
            for s in ["VWAP_PULLBACK", "DIRECTIONAL_BUY", "ORB_BREAKOUT", "MOMENTUM_FADE"]:
                if s not in allowed:
                    allowed.append(s)

        # â”€â”€ CHECK EXITS â”€â”€
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
                executor._save()

            should_exit, reason, action = risk.check_exit(pos, current_value)
            if should_exit:
                if action == "ROLL":
                    logger.info(f"ROLLING: {pos['symbol']} {reason}")
                result = executor.close_position(pos["id"], current)
                if result["status"] == "CLOSED":
                    risk.record_trade(result["pnl"])
                    risk.open_positions -= 1
                    logger.info(f"EXIT: {reason}")

        # â”€â”€ CHECK SIGNALS â”€â”€
        can_trade, trade_reason = risk.can_trade()
        if can_trade and allowed:
            for sym in ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD", "NFLX", "COIN", "BA", "JPM", "XOM"]:
                snap_5m = data_engine.get_snapshot(sym)
                snap_1m = data_engine.get_entry_snapshot(sym)
                if not snap_5m:
                    continue

                new_signals = signals.scan(
                    snapshot_5m=snap_5m,
                    snapshot_1m=snap_1m,
                    allowed_strategies=allowed,
                    gex_profile=gex_cache.get(sym),
                    breadth=breadth_cache,
                    expected_move=em_cache.get(sym),
                )

                for signal in new_signals:
                    can_still, _ = risk.can_trade()
                    if not can_still:
                        break

                    max_cost = risk.get_position_size(signal["confidence"])
                    structure = signal.get("structure", "LONG_OPTION")

                    rr_str = ""
                    if signal.get("rr_ratio"):
                        rr_str = f" RR:{signal['rr_ratio']}:1"

                    logger.info(
                        f"SIGNAL: {signal['type']} "
                        f"{signal['direction']} {signal['symbol']} "
                        f"conf:{signal['confidence']}{rr_str} "
                        f"| {signal['reason']}"
                    )

                    if structure == "LONG_OPTION":
                        # Theta acceleration filter
                        ok, tf_reason = picker.should_allow_buy(signal["confidence"])
                        if not ok:
                            logger.info(f"  Blocked: {tf_reason}")
                            continue

                        # Breadth hard block
                        if breadth_cache:
                            b_ok, b_reason = internals.confirms_direction(
                                signal["direction"], breadth_cache
                            )
                            if not b_ok:
                                logger.info(f"  Blocked: {b_reason}")
                                continue

                        contract = picker.pick(
                            sym, signal["direction"], max_cost, structure
                        )
                        if contract:
                            result = executor.open_position(signal, contract, max_cost)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No contract")

                    elif structure in ("CREDIT_SPREAD", "IRON_CONDOR"):
                        spread = picker.pick_credit_spread(
                            sym, signal["direction"], max_cost,
                            snap_5m.get("atr", 1),
                        )
                        if spread:
                            signal["_spread"] = spread
                            result = executor.open_credit_position(signal, spread)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No spread")

                    elif structure in ("NAKED_PUT", "NAKED_CALL"):
                        naked = picker.pick_naked(
                            sym, signal["direction"],
                            max_cost, target_delta=0.15
                        )
                        if naked:
                            result = executor.open_naked_position(signal, naked)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No naked contract")

                    elif structure == "STRADDLE":
                        straddle = picker.pick_straddle(sym, max_cost)
                        if straddle:
                            result = executor.open_straddle_position(signal, straddle)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No straddle")

                    elif structure == "STRANGLE":
                        strangle = picker.pick_strangle(sym, max_cost)
                        if strangle:
                            result = executor.open_straddle_position(signal, strangle)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No strangle")

                    elif structure == "RATIO_SPREAD":
                        ratio = picker.pick_ratio_spread(
                            sym, signal["direction"], max_cost
                        )
                        if ratio:
                            result = executor.open_ratio_position(signal, ratio)
                            if result["status"] == "FILLED":
                                risk.open_positions += 1
                        else:
                            logger.info("  No ratio spread")

        # â”€â”€ STATUS (every 60s) â”€â”€
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

            day_str = day_class.day_type if classified else "BUILDING"
            logger.info(
                f"[{now.strftime('%H:%M:%S')}] {spy_str} | "
                f"{gex_str} Day:{day_str} | "
                f"T:{s['today_trades']} "
                f"Open:{s['open_positions']} "
                f"P&L:${s['today_pnl']:+,.0f}"
            )

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
