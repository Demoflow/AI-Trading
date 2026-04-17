"""
Small Cap Momentum Trader — Main Entry Point
Ross Cameron / Warrior Trading style.

Strategy overview:
  1. Pre-market (7:00–8:30 AM CT): scan for gap candidates with news catalysts
  2. Market open (8:30–10:30 AM CT): monitor top candidates via streaming,
     wait for bull flag / ABCD / ORB pattern + order flow confirmation
  3. Entry: breakout of prior candle high, confirmed by order flow score >= 65
  4. Exit: scale out 1/3 at +10%, 1/3 at +20%, trail the rest with 5% stop
  5. Hard rules: max $250 risk/trade, $500 daily loss limit, 3-loss circuit breaker

Stage 9 (current): Full system — all stages integrated.
  - Pre-market: gap scanner + catalyst engine → ranked candidates
  - Market open: streaming (L1/L2/charts) → order flow + patterns → executor
  - Risk: $250/trade, $500 daily, 3-strike, 2:1 R:R, no averaging down
  - Exit: scale out 1/3 at +10%, 1/3 at +20%, trail 5% on remainder
"""

import os
import sys
import json
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None   # fallback — assumes system clock is already in CT

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()

from utils.logging_setup import setup_logging
from smallcap.config import (
    STARTING_EQUITY, MARKET_OPEN, PRIME_WINDOW_END,
    LATE_ENTRY_CUTOFF, EOD_FLATTEN, MARKET_CLOSE,
    PREMARKET_SCAN_START, MAX_DAILY_LOSS, MAX_RISK_PER_TRADE,
    PREMARKET_SCAN_INTERVAL_SEC, SESSION_CANDIDATES_PATH,
)
from smallcap.universe import UniverseManager
from smallcap.gap_scanner import GapScanner
from smallcap.catalyst_engine import CatalystEngine
from smallcap.stream_manager import StreamManager
from smallcap.order_flow import OrderFlowEngine
from smallcap.pattern_engine import PatternEngine
from smallcap.risk_manager import SmallCapRiskManager
from smallcap.executor import TradeExecutor
from smallcap.dux_pattern_engine import DuxPatternEngine
from smallcap.dux_risk_manager import DuxRiskManager
from smallcap.dux_executor import DuxExecutor
from smallcap.dux_config import DUX_START_CT, DUX_LATE_ENTRY_CUTOFF_CT, DUX_MAX_DAILY_LOSS
from smallcap.market_character import analyze_market_character


def _hour_ct() -> float:
    """Return current time as decimal CT hours, timezone-aware."""
    if _CT_TZ:
        n = datetime.now(tz=_CT_TZ)
    else:
        n = datetime.now()
    return n.hour + n.minute / 60.0 + n.second / 3600.0


# ── Agent override helpers ─────────────────────────────────────────────────────
_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "agent_overrides.json"
)
_overrides_mtime: float = 0.0
_overrides_cache: dict = {}


def _load_overrides() -> dict:
    global _overrides_mtime, _overrides_cache
    try:
        mtime = os.path.getmtime(_OVERRIDES_PATH)
        if mtime != _overrides_mtime:
            with open(_OVERRIDES_PATH, "r") as f:
                _overrides_cache = json.load(f)
            _overrides_mtime = mtime
    except Exception:
        pass
    return _overrides_cache


def _save_overrides(data: dict):
    try:
        with open(_OVERRIDES_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def run():
    setup_logging()

    logger.info("=" * 65)
    logger.info("SMALL CAP DUAL-STRATEGY TRADER")
    logger.info(f"  Ross Cameron    : Bull Flag | ABCD | ORB  (long breakouts)")
    logger.info(f"  Steven Dux      : FRD | Spike Short | H&S | Dip Panic")
    logger.info(f"  Starting equity : ${STARTING_EQUITY:,.2f}")
    logger.info(f"  Max risk/trade  : ${MAX_RISK_PER_TRADE:,.2f}")
    logger.info(f"  Daily loss limit: ${MAX_DAILY_LOSS:,.2f}")
    logger.info(f"  Edge            : Gap + Catalyst + Order Flow + Exhaustion")
    logger.info("=" * 65)

    # ── SCHWAB CONNECTION ──────────────────────────────────────────────────────
    try:
        from data.broker.schwab_auth import get_schwab_client, get_account_hash
        import httpx
        client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.error(f"Schwab connection failed: {e}")
        return

    # Get account info — account_id (number) for streaming, account_hash for orders
    account_id   = None
    account_hash = None
    try:
        resp = client.get_account_numbers()
        assert resp.status_code == httpx.codes.OK
        accounts = resp.json()
        if accounts:
            account_id   = accounts[0]["accountNumber"]
            account_hash = accounts[0]["hashValue"]
        logger.info(f"Account: {account_id}")
    except Exception as e:
        logger.error(f"Could not fetch account numbers: {e}")

    # ── MARKET CHARACTER ANALYSIS ─────────────────────────────────────────────
    # Assess session regime (hot/normal/cold/avoid) using VIX + SPY pre-market.
    # Returns an adjusted OFE threshold so we only lower the bar on genuinely
    # favorable days and raise it when conditions are choppy.
    market = analyze_market_character(client)
    ofe_threshold = market.ofe_threshold
    logger.info(
        f"Session profile: {market.regime.upper()} | "
        f"OFE threshold set to {ofe_threshold} | {market.note}"
    )

    # ── UNIVERSE ───────────────────────────────────────────────────────────────
    universe = UniverseManager()
    tickers = universe.load()
    logger.info(f"Scanning universe: {len(tickers)} tickers")

    # Start float refresh in background — non-blocking, fills cache over time
    universe.refresh_floats(background=True)

    # ── CATALYST ENGINE ────────────────────────────────────────────────────────
    catalyst = CatalystEngine(universe)
    catalyst.start()   # begins background polling immediately

    # ── GAP SCANNER ────────────────────────────────────────────────────────────
    scanner = GapScanner(client, universe)

    # Bootstrap avg volumes synchronously — needed for relative volume filter.
    # This runs once at startup and takes ~30–90s for a full universe.
    # Skip if we're already past the prime window (late startup edge case).
    h = _hour_ct()
    if h < PRIME_WINDOW_END:
        scanner.bootstrap_avg_volumes()
    else:
        logger.warning(
            "Past prime window at startup — skipping avg volume bootstrap. "
            "Relative volume filter will be unavailable this session."
        )

    # ── SESSION LOOP ───────────────────────────────────────────────────────────
    h = _hour_ct()
    logger.info(f"Current time: {datetime.now().strftime('%H:%M CT')}")

    # Wait for pre-market scan window if we're early
    if h < PREMARKET_SCAN_START:
        wait_until = PREMARKET_SCAN_START
        logger.info(
            f"Waiting for pre-market scan window "
            f"(starts {wait_until:.1f} CT = {wait_until + 1:.1f} ET)..."
        )
        while _hour_ct() < PREMARKET_SCAN_START:
            time.sleep(30)

    # ── PRE-MARKET PHASE (7:00–8:30 AM CT) ────────────────────────────────────
    if _hour_ct() < MARKET_OPEN:
        logger.info("─" * 65)
        logger.info("PRE-MARKET SCAN PHASE")
        logger.info("  Looking for: gap 10%+, float <20M, rel vol 5x+, catalyst")
        logger.info("─" * 65)

        while _hour_ct() < MARKET_OPEN:
            h = _hour_ct()

            # Run the live gap scan with live catalyst scores
            candidates = scanner.scan(catalyst_scores=catalyst.get_scores())
            _save_candidates(candidates)

            if candidates:
                logger.info(
                    f"[{datetime.now().strftime('%H:%M')}] "
                    f"{len(candidates)} gap candidate(s) | "
                    f"{MARKET_OPEN - h:.2f}h until open"
                )
                for i, c in enumerate(candidates, 1):
                    logger.info(
                        f"  #{i} {c['symbol']:6s}  "
                        f"gap={c['gap_pct']:+6.1f}%  "
                        f"price=${c['price']:.2f}  "
                        f"vol={c['volume']:>10,}  "
                        f"rvol={c['rel_volume'] or 'n/a'}x  "
                        f"float={_fmt_float(c['float'])}"
                    )
            else:
                logger.info(
                    f"[{datetime.now().strftime('%H:%M')}] "
                    f"No gap candidates yet | "
                    f"{MARKET_OPEN - h:.2f}h until open"
                )

            # Taper scan interval as market open approaches:
            # > 30 min away → 60s, 15–30 min → 30s, < 15 min → 15s
            minutes_to_open = (MARKET_OPEN - _hour_ct()) * 60
            if minutes_to_open > 30:
                time.sleep(60)
            elif minutes_to_open > 15:
                time.sleep(30)
            else:
                time.sleep(15)

    # ── MARKET HOURS PHASE ─────────────────────────────────────────────────────
    logger.info("─" * 65)
    logger.info("MARKET OPEN — entering prime trading window")
    logger.info(f"  Prime window ends: {PRIME_WINDOW_END:.1f} CT ({PRIME_WINDOW_END + 1:.1f} ET)")
    logger.info("─" * 65)

    # Run one final scan at open to lock in the candidate list
    candidates = scanner.scan(catalyst_scores=catalyst.get_scores())
    _save_candidates(candidates)
    if candidates:
        logger.info(f"Opening bell candidates ({len(candidates)}):")
        for i, c in enumerate(candidates, 1):
            logger.info(
                f"  #{i} {c['symbol']:6s}  "
                f"gap={c['gap_pct']:+6.1f}%  "
                f"price=${c['price']:.2f}  "
                f"catalyst={c['catalyst_score']}"
            )

    # ── STREAMING ─────────────────────────────────────────────────────────────
    candidate_symbols = [c["symbol"] for c in candidates]
    risk_mgr  = SmallCapRiskManager()
    dux_risk  = DuxRiskManager()
    stream    = None
    ofe       = None
    pe        = None
    dux_pe    = None
    if account_id and candidate_symbols:
        stream = StreamManager(client, account_id)
        stream.start(candidate_symbols)
        logger.info(
            f"Streaming started for: {', '.join(candidate_symbols)}"
        )
        # ── Ross: order flow + pattern engine ─────────────────────────────
        ofe = OrderFlowEngine(stream)
        ofe.start()
        pe = PatternEngine(stream)
        pe.start()
        for c in candidates:
            resistance = c["prior_close"] * 1.05  # rough: 5% above prior close
            ofe.start_watching(c["symbol"], resistance=resistance)
            pe.watch(c["symbol"])

        # ── Dux: pattern engine (no OFE — pure price action) ──────────────
        dux_pe = DuxPatternEngine(stream)
        dux_pe.start()
        for c in candidates:
            dux_pe.watch(c["symbol"])
            dux_pe.set_candidate_meta(c["symbol"], {
                "prev_day_change_pct": c.get("prev_day_change_pct", 0),
                "prior_close":         c.get("prior_close", 0),
                "premarket_vol":       c.get("volume", 0),
                "float":               c.get("float"),
            })
        logger.info("Dux pattern engine started")
    elif not account_id:
        logger.warning("Streaming disabled — account_id unavailable")
    else:
        logger.info("No candidates — streaming not started")

    # ── EXECUTORS ─────────────────────────────────────────────────────────────
    executor = None
    dux_exec = None
    if stream and account_hash:
        executor = TradeExecutor(client, account_hash, risk_mgr, stream)
        logger.info(f"Ross executor ready | account_hash={account_hash[:8]}...")
        dux_exec = DuxExecutor(client, account_hash, dux_risk, stream)
        logger.info("Dux executor ready")

    # Combined daily loss limit: if both strategies together exceed this,
    # halt both regardless of individual limits.
    # Ross max = $500 + Dux max = $750 = $1,250 unconstrained.  A combined
    # hard stop of $750 prevents one strategy from subsidising the other.
    _COMBINED_DAILY_LOSS_LIMIT = MAX_DAILY_LOSS + DUX_MAX_DAILY_LOSS * 0.5  # $875

    # ── MARKET HOURS MAIN LOOP ─────────────────────────────────────────────────
    _last_log_minute          = -1
    _last_screener_check      = 0.0   # monotonic timestamp
    _last_watchdog_check      = 0.0
    _last_market_char_refresh = 0.0   # market character (VIX/SPY) refresh
    _last_zero_cand_scan      = 0.0   # zero-candidate recovery rescan
    _SCREENER_CHECK_INTERVAL  = 30    # check screener hits every 30s
    _WATCHDOG_CHECK_INTERVAL  = 60    # check stream health every 60s
    _STREAM_STALE_SEC         = 90    # alert if no L1 message for this long
    _MARKET_CHAR_INTERVAL     = 1800  # refresh VIX/SPY regime every 30 min
    _ZERO_CAND_SCAN_INTERVAL  = 120   # re-scan for candidates every 2 min if none found

    try:
        while _hour_ct() < EOD_FLATTEN:
            h = _hour_ct()
            now = datetime.now()
            current_minute = now.hour * 60 + now.minute

            try:
                # ── Agent override check ───────────────────────────────────────
                _ov = _load_overrides()

                if _ov.get("flatten_all"):
                    logger.warning("[Agent] FLATTEN ALL received — flattening all positions")
                    if executor:
                        executor.flatten_all(reason="agent_flatten")
                    if dux_exec:
                        dux_exec.flatten_all(reason="agent_flatten")
                    _ov["flatten_all"] = False
                    _save_overrides(_ov)

                _smallcap_paused = _ov.get("smallcap_paused", False)
                _blocked_syms    = set(s.upper() for s in _ov.get("blocked_symbols", []))
                _ofe_ov          = _ov.get("ofe_override")
                _raw_ofe         = _ofe_ov if _ofe_ov is not None else ofe_threshold
                # Clamp override to sane range: floor 40 (minimum edge filter),
                # ceiling 85 (above this almost no trades would pass).
                _eff_ofe         = max(40, min(85, _raw_ofe))

                # ── Position management — every tick ──────────────────────────
                if executor:
                    executor.manage_positions()
                if dux_exec:
                    dux_exec.manage_positions()

                # ── Screener: promote new gap candidates ──────────────────────
                _now_mono = time.monotonic()
                if stream and _now_mono - _last_screener_check >= _SCREENER_CHECK_INTERVAL:
                    _last_screener_check = _now_mono
                    for hit in stream.get_screener_hits():
                        sym = hit["symbol"]
                        if sym not in candidate_symbols:
                            universe.add_ticker(sym)
                            candidate_symbols.append(sym)
                            stream.subscribe_symbols([sym])
                            if ofe:
                                ofe.start_watching(sym, resistance=hit["price"] * 1.05)
                            if pe:
                                pe.watch(sym)
                            if dux_pe:
                                dux_pe.watch(sym)
                                dux_pe.set_candidate_meta(sym, {
                                    "prev_day_change_pct": 0,
                                    "prior_close":         hit["price"] * 0.90,
                                    "premarket_vol":       hit["volume"],
                                    "float":               None,
                                })
                            logger.info(
                                f"Screener → new candidate: {sym} "
                                f"+{hit['pct_change']:.1f}% @ ${hit['price']:.2f} "
                                f"vol={hit['volume']:,}"
                            )

                # ── Stream watchdog ───────────────────────────────────────────
                if stream and _now_mono - _last_watchdog_check >= _WATCHDOG_CHECK_INTERVAL:
                    _last_watchdog_check = _now_mono
                    stale = stream.seconds_since_last_message()
                    if stale > _STREAM_STALE_SEC:
                        logger.warning(
                            f"STREAM WATCHDOG: no L1 message for {stale:.0f}s — "
                            f"connected={stream.is_connected()} "
                            f"(stream will auto-reconnect if disconnected)"
                        )
                    elif not stream.is_connected():
                        logger.warning("STREAM WATCHDOG: stream shows disconnected — reconnecting")

                # ── Combined daily loss limit ─────────────────────────────────
                combined_pnl = risk_mgr.get_daily_pnl() + dux_risk.get_daily_pnl()
                if combined_pnl <= -_COMBINED_DAILY_LOSS_LIMIT:
                    logger.warning(
                        f"COMBINED DAILY LOSS LIMIT HIT "
                        f"(combined P&L=${combined_pnl:+.2f} ≤ "
                        f"-${_COMBINED_DAILY_LOSS_LIMIT:.0f}) — "
                        f"flattening all positions and halting both strategies"
                    )
                    if executor:
                        executor.flatten_all(reason="combined_loss_limit")
                    if dux_exec:
                        dux_exec.flatten_all(reason="combined_loss_limit")
                    break

                # ── Mid-session market character refresh (VIX/SPY) ───────────
                # Re-assess regime every 30 minutes so a sudden market reversal
                # (e.g. Fed announcement, major macro news) tightens the OFE gate.
                if _now_mono - _last_market_char_refresh >= _MARKET_CHAR_INTERVAL:
                    _last_market_char_refresh = _now_mono
                    try:
                        market = analyze_market_character(client)
                        ofe_threshold = market.ofe_threshold
                        logger.info(
                            f"[MarketChar refresh] {market.regime.upper()} | "
                            f"OFE threshold → {ofe_threshold} | {market.note}"
                        )
                    except Exception as _mc_err:
                        logger.warning(
                            f"Market character refresh failed (using last value "
                            f"{ofe_threshold}): {_mc_err}"
                        )

                # ── Zero-candidate recovery rescan ────────────────────────────
                # If streaming never started (no gap candidates at open) or
                # the candidate list is thin, periodically re-scan for late
                # breakouts so the session isn't silently idle.
                if (h <= LATE_ENTRY_CUTOFF
                        and _now_mono - _last_zero_cand_scan >= _ZERO_CAND_SCAN_INTERVAL
                        and account_id):
                    _last_zero_cand_scan = _now_mono
                    late_cands = scanner.scan(catalyst_scores=catalyst.get_scores())
                    for c in late_cands:
                        sym = c["symbol"]
                        if sym not in candidate_symbols:
                            candidate_symbols.append(sym)
                            if stream is None and account_id:
                                # First candidate discovered after open — start streaming
                                stream = StreamManager(client, account_id)
                                stream.start([sym])
                                ofe = OrderFlowEngine(stream)
                                ofe.start()
                                pe = PatternEngine(stream)
                                pe.start()
                                dux_pe = DuxPatternEngine(stream)
                                dux_pe.start()
                                if account_hash:
                                    executor = TradeExecutor(client, account_hash, risk_mgr, stream)
                                    dux_exec = DuxExecutor(client, account_hash, dux_risk, stream)
                                logger.info(
                                    f"Zero-candidate recovery: started streaming for {sym}"
                                )
                            elif stream:
                                stream.subscribe_symbols([sym])
                            if ofe:
                                ofe.start_watching(sym, resistance=c.get("prior_close", c["price"]) * 1.05)
                            if pe:
                                pe.watch(sym)
                            if dux_pe:
                                dux_pe.watch(sym)
                                dux_pe.set_candidate_meta(sym, {
                                    "prev_day_change_pct": c.get("prev_day_change_pct", 0),
                                    "prior_close":         c.get("prior_close", 0),
                                    "premarket_vol":       c.get("volume", 0),
                                    "float":               c.get("float"),
                                })
                            logger.info(
                                f"Late breakout scan → new candidate: {sym} "
                                f"gap={c['gap_pct']:+.1f}% @ ${c['price']:.2f}"
                            )

                # ── Ross entry signals ────────────────────────────────────────
                if (not _smallcap_paused
                        and executor and pe and ofe
                        and h <= LATE_ENTRY_CUTOFF
                        and not risk_mgr.get_status()["daily_halted"]):
                    for sym, signals in pe.get_all_signals().items():
                        if sym in _blocked_syms:
                            continue
                        for sig in signals:
                            if _has_conflict(sym, risk_mgr, dux_risk):
                                logger.debug(f"Ross blocked {sym}: Dux holds position")
                                continue
                            score_dict = ofe.get_score(sym)
                            ofe_score  = score_dict["composite"] if score_dict else 0
                            if ofe_score >= _eff_ofe:
                                executor.enter(sig, ofe_score)
                                break

                # ── Dux entry signals ─────────────────────────────────────────
                if (not _smallcap_paused
                        and dux_exec and dux_pe
                        and DUX_START_CT <= h <= DUX_LATE_ENTRY_CUTOFF_CT
                        and not dux_risk.is_halted()):
                    for sym, signals in dux_pe.get_all_signals().items():
                        if sym in _blocked_syms:
                            continue
                        for sig in signals:
                            if _has_conflict(sym, risk_mgr, dux_risk):
                                logger.debug(f"[Dux] Blocked {sym}: Ross holds position")
                                continue
                            dux_exec.enter(sig)
                            break

                # ── Periodic status log ───────────────────────────────────────
                if h > LATE_ENTRY_CUTOFF:
                    if current_minute % 30 == 0 and current_minute != _last_log_minute:
                        _last_log_minute = current_minute
                        status     = risk_mgr.get_status()
                        dux_status = dux_risk.get_status()
                        logger.info(
                            f"[{now.strftime('%H:%M')}] Past entry cutoff | "
                            f"Ross P&L=${status['daily_pnl']:+.2f} pos={status['open_positions']} | "
                            f"Dux P&L=${dux_status['daily_pnl']:+.2f} pos={dux_status['open_positions']}"
                        )
                else:
                    if current_minute % 15 == 0 and current_minute != _last_log_minute:
                        _last_log_minute = current_minute
                        window     = "PRIME" if h < PRIME_WINDOW_END else "EXTENDED"
                        status     = risk_mgr.get_status()
                        dux_status = dux_risk.get_status()
                        logger.info(
                            f"[{now.strftime('%H:%M')}] {window} | "
                            f"Ross P&L=${status['daily_pnl']:+.2f} pos={status['open_positions']} "
                            f"streak={status['consecutive_loss']} | "
                            f"Dux P&L=${dux_status['daily_pnl']:+.2f} pos={dux_status['open_positions']} "
                            f"wr={dux_status['win_rate']:.0%}"
                        )

            except Exception as _tick_err:
                # Log the error but keep the loop alive — a single bad tick
                # (None quote, network blip, unexpected API response) must not
                # take down an otherwise healthy session with open positions.
                logger.error(
                    f"Main loop tick error (session continuing): "
                    f"{type(_tick_err).__name__}: {_tick_err}",
                    exc_info=True,
                )

            time.sleep(1)   # 1s main loop tick

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received — shutting down cleanly")
    except Exception as _fatal:
        logger.critical(
            f"FATAL error in main loop — forcing EOD flatten: "
            f"{type(_fatal).__name__}: {_fatal}",
            exc_info=True,
        )
    finally:
        # ── EOD / crash flatten — always runs ─────────────────────────────────
        logger.info("Flattening all positions...")
        if executor:
            try:
                executor.flatten_all(reason="EOD")
            except Exception as e:
                logger.error(f"Error flattening Ross positions: {e}")
        if dux_exec:
            try:
                dux_exec.flatten_all(reason="EOD")
            except Exception as e:
                logger.error(f"Error flattening Dux positions: {e}")

        # ── Stop all background engines ────────────────────────────────────
        for engine, name in [
            (dux_pe,  "DuxPatternEngine"),
            (pe,      "PatternEngine"),
            (ofe,     "OrderFlowEngine"),
            (stream,  "StreamManager"),
        ]:
            if engine:
                try:
                    engine.stop()
                except Exception as e:
                    logger.warning(f"Error stopping {name}: {e}")
        try:
            catalyst.stop()
        except Exception as e:
            logger.warning(f"Error stopping CatalystEngine: {e}")

        # ── Session summary ────────────────────────────────────────────────
        try:
            final_ross   = risk_mgr.get_status()
            final_dux    = dux_risk.get_status()
            combined_pnl = final_ross["daily_pnl"] + final_dux["daily_pnl"]
            logger.info("=" * 65)
            logger.info("SMALL CAP DUAL-STRATEGY TRADER — SESSION COMPLETE")
            logger.info(f"  ── Ross Cameron ──────────────────────────────────")
            logger.info(f"  Trades today    : {final_ross['trades_today']}")
            logger.info(f"  Daily P&L       : ${final_ross['daily_pnl']:+.2f}")
            logger.info(f"  Consecutive loss: {final_ross['consecutive_loss']}")
            logger.info(f"  ── Steven Dux ────────────────────────────────────")
            logger.info(f"  Trades today    : {final_dux['trades_today']}")
            logger.info(f"  Daily P&L       : ${final_dux['daily_pnl']:+.2f}")
            logger.info(f"  Win rate        : {final_dux['win_rate']:.0%}")
            logger.info(f"  Error mode      : {final_dux['error_mode']} trades remaining")
            logger.info(f"  ── Combined ──────────────────────────────────────")
            logger.info(f"  Combined P&L    : ${combined_pnl:+.2f}")
            logger.info("=" * 65)
        except Exception as e:
            logger.warning(f"Could not print session summary: {e}")


def _has_conflict(symbol: str, ross_risk, dux_risk) -> bool:
    """
    Returns True if either the Ross or Dux system already holds an open
    position in this symbol.  Prevents the two systems from taking
    opposite-direction positions in the same symbol within one account.
    """
    return (symbol in ross_risk.get_positions()
            or symbol in dux_risk.get_positions())


def _save_candidates(candidates: list):
    """Persist the current gap candidates for the dashboard to read."""
    try:
        os.makedirs(os.path.dirname(SESSION_CANDIDATES_PATH), exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(),
            "candidates": [
                {k: v for k, v in c.items() if not k.startswith("_")}
                for c in candidates
            ],
        }
        with open(SESSION_CANDIDATES_PATH, "w") as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass


def _fmt_float(float_shares) -> str:
    if float_shares is None:
        return "unknown"
    if float_shares >= 1_000_000:
        return f"{float_shares / 1_000_000:.1f}M"
    if float_shares >= 1_000:
        return f"{float_shares / 1_000:.0f}K"
    return str(float_shares)


if __name__ == "__main__":
    run()
