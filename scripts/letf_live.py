"""
LETF Swing System - Main Live Script v2 (Smart Timing).

Changes from v1:
  - Entries move into the monitoring loop; no more one-shot morning batch
  - LETFSmartEntry gates each entry: pullbacks/bounces, volume, VIX stability, time windows
  - Sector re-scan every 90 min: discovers new setups, drops stale ones
  - LETFExitManager.check_exit_with_timing(): open protection, EOD tightening, pre-EOD capture
"""
import sys, os, time, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from utils.logging_setup import setup_logging
from data.broker.schwab_auth import get_schwab_client

from letf.universe import SECTORS, UNDERLYINGS
from letf.sector_analyzer import SectorAnalyzer
from letf.executor import LETFExecutor
from letf.exit_manager import LETFExitManager
from letf.smart_entry import LETFSmartEntry
from aggressive.portfolio_analyst import PortfolioAnalyst
from letf.earnings_calendar import EarningsCalendar
from letf.earnings_cluster import SectorEarningsCluster


RESCAN_INTERVAL = 90 * 60   # Re-scan all sectors every 90 minutes


def run(live=False):
    setup_logging()
    logger.warning("=" * 60)
    logger.warning("LETF PCRA SYSTEM — DISABLED")
    logger.warning("Re-enable manually before running.")
    logger.warning("=" * 60)
    return
    client = get_schwab_client()
    logger.info("Schwab connected")

    with open("config/letf_config.json") as _f:
        config = json.load(_f)
    executor = LETFExecutor(client, live=live, config_path="config/letf_config.json", portfolio_path="config/letf_portfolio.json")
    exits    = LETFExitManager(config)
    smart    = LETFSmartEntry(client)
    portfolio_analyst = PortfolioAnalyst(client)
    last_analyst_run  = 0
    analyzer = SectorAnalyzer(client)
    earnings = EarningsCalendar(client)
    clusters = SectorEarningsCluster()

    logger.info("=" * 60)
    logger.info("LEVERAGED ETF SWING SYSTEM v2 (Smart Timing)")
    logger.info(f"Mode: {'LIVE' if live else 'PAPER'}")
    bal = executor.get_real_balance()
    if bal:
        config["equity"] = bal["equity"]
        executor.portfolio["equity"] = bal["equity"]
        logger.info(f"Equity: ${bal['equity']:,.2f} (live sync)")
    else:
        logger.info(f"Equity: ${config['equity']:,.2f} (from config)")
    logger.info(f"Conviction minimum: {config['min_conviction']}")
    logger.info(f"Max position: {config['max_position_pct']:.0%} = ${config['equity'] * config['max_position_pct']:,.0f}")
    logger.info("=" * 60)

    # ── HALT CHECKS ──
    daily_halt  = False
    weekly_halt = False
    if live:
        if executor.check_weekly_drawdown():
            weekly_halt = True
            logger.warning("WEEKLY DRAWDOWN HALT - reducing all positions by 50%")
        bal = executor.get_real_balance()
        if bal:
            daily_change = bal["equity"] - config["equity"]
            daily_pct    = daily_change / config["equity"]
            if daily_pct < -config["daily_loss_halt_pct"]:
                daily_halt = True
                logger.warning(f"DAILY HALT: equity down {daily_pct:.1%} (${daily_change:+,.0f}). No new trades today.")

    # ── SECTOR SCANNER (returns candidates dict) ──
    def scan_all_sectors():
        """
        Full sector scan with earnings/cluster boosts.
        Returns candidates dict keyed by (sector_name, direction).
        Each value is a trade template dict ready for smart-entry gating.
        """
        analyzer.reset_cache()
        results = []

        for sector_name, sector_info in SECTORS.items():
            result = analyzer.analyze_sector(sector_name, sector_info)
            bull   = result["bull_score"]
            bear   = result["bear_score"]

            # Earnings boost
            try:
                earn_boost, earn_dir, earn_detail = earnings.get_earnings_boost(sector_name)
                if earn_boost > 0:
                    if earn_dir == "BULL":
                        bull += earn_boost
                    elif earn_dir == "BEAR":
                        bear += earn_boost
                    if earn_detail:
                        logger.info(
                            f"    EARNINGS: {earn_detail['symbol']} in {earn_detail['days_until']}d "
                            f"flow={earn_dir} conv={earn_detail['flow_conviction']}"
                        )
            except Exception:
                pass

            # Cluster boost
            try:
                cluster_boost, cluster_stocks = clusters.get_cluster_boost(sector_name)
                if cluster_boost > 0:
                    bull += cluster_boost
                    bear += cluster_boost
                    names = [s["symbol"] for s in cluster_stocks]
                    logger.info(f"    CLUSTER: {sector_name} {len(cluster_stocks)} earnings ({', '.join(names)}) +{cluster_boost}pts")
            except Exception:
                pass

            result["bull_score"] = bull
            result["bear_score"] = bear
            results.append(result)

            best  = "BULL" if bull > bear else "BEAR"
            score = max(bull, bear)
            logger.info(
                f"  {sector_name:<12} {best} {score:>3} | "
                f"bull={bull} bear={bear} | "
                f"{result['signals'].get('structure', '?')} "
                f"RS={result['signals'].get('rs_vs_spy', 0):+.1f}% "
                f"Mom5d={result['signals'].get('mom_5d', 0):+.1f}%"
            )

        new_candidates = {}
        for r in results:
            sector_name  = r["sector"]
            sector_info  = SECTORS[sector_name]
            sector_min_conv = sector_info.get("min_conviction", config["min_conviction"])
            sector_max_pct  = sector_info.get("max_position_pct", config["max_position_pct"])
            is_single    = sector_info.get("single_stock", False)
            underlying   = sector_info["underlying"]

            if r["bull_score"] >= sector_min_conv:
                etf   = sector_info["bull"]
                quote = analyzer._get_quote(etf)
                if quote:
                    price = quote.get("lastPrice", 0)
                    if price > 0:
                        max_cost = config["equity"] * sector_max_pct
                        if is_single:
                            max_cost = min(max_cost, config["equity"] * 0.07)
                        qty = int(max_cost / price)
                        if qty > 0:
                            new_candidates[(sector_name, "BULL")] = {
                                "symbol":       etf,
                                "direction":    "BULL",
                                "sector":       sector_name,
                                "underlying":   underlying,
                                "score":        r["bull_score"],
                                "price":        price,
                                "qty":          qty,
                                "cost":         round(qty * price, 2),
                                "leverage":     sector_info["leverage"],
                                "signals":      r["signals"],
                                "single_stock": is_single,
                                "max_hold_days": sector_info.get("max_hold_days", config["max_hold_days"]),
                            }

            if r["bear_score"] >= sector_min_conv:
                etf   = sector_info["bear"]
                quote = analyzer._get_quote(etf)
                if quote:
                    price = quote.get("lastPrice", 0)
                    if price > 0:
                        max_cost = config["equity"] * sector_max_pct
                        qty = int(max_cost / price)
                        if qty > 0:
                            new_candidates[(sector_name, "BEAR")] = {
                                "symbol":       etf,
                                "direction":    "BEAR",
                                "sector":       sector_name,
                                "underlying":   underlying,
                                "score":        r["bear_score"],
                                "price":        price,
                                "qty":          qty,
                                "cost":         round(qty * price, 2),
                                "leverage":     sector_info["leverage"],
                                "signals":      r["signals"],
                                "single_stock": is_single,
                                "max_hold_days": sector_info.get("max_hold_days", config["max_hold_days"]),
                            }

        return new_candidates

    # ── INITIAL SCAN ──
    logger.info("Initial sector scan...")
    candidates     = scan_all_sectors()
    last_scan_time = time.time()

    logger.info(f"\nCandidates: {len(candidates)} — awaiting smart entry conditions")
    for key, t in sorted(candidates.items(), key=lambda x: x[1]["score"], reverse=True):
        logger.info(
            f"  {t['direction']:<4} {t['symbol']:<5} ({t['sector']}) "
            f"score={t['score']} est ${t['cost']:,.0f}"
        )

    # ── MONITORING LOOP ──
    logger.info("\nEntering smart monitoring loop...")
    cycle = 0
    while True:
        from datetime import datetime
        now    = datetime.now()
        hour_ct = now.hour + now.minute / 60.0

        # Market hours check (8:30 AM – 3:00 PM CT)
        if hour_ct < 8.5 or hour_ct > 15.0:
            if hour_ct > 15.0:
                logger.info("Market closed.")
                if live:
                    executor.sync_equity()
                s = executor.get_summary()
                logger.info(
                    f"  Open: {s['open']} | Deployed: ${s['deployed']:,.0f} | "
                    f"Closed: {s['closed']} | P&L: ${s['total_pnl']:+,.0f} | "
                    f"WR: {s['win_rate']:.0f}%"
                )
                break
            time.sleep(60)
            continue

        # ── PERIODIC RE-SCAN (every 90 min) ──
        if time.time() - last_scan_time > RESCAN_INTERVAL:
            logger.info("Re-scanning sectors (90-min refresh)...")
            new_cands = scan_all_sectors()

            # Drop candidates that have lost conviction
            for key in list(candidates.keys()):
                if key not in new_cands:
                    dropped = candidates.pop(key)
                    smart.reset(dropped["symbol"])
                    logger.info(f"  DROPPED: {dropped['symbol']} — conviction gone")

            # Add new setups; refresh scores on existing ones
            for key, t in new_cands.items():
                if key not in candidates:
                    candidates[key] = t
                    logger.info(f"  NEW: {t['symbol']} ({t['direction']}) score={t['score']}")
                else:
                    candidates[key]["score"] = t["score"]
                    candidates[key]["price"] = t["price"]

            last_scan_time = time.time()

        # ── SMART ENTRY ATTEMPTS ──
        if not (daily_halt or weekly_halt) and candidates:
            existing     = {p["symbol"] for p in executor.portfolio["positions"] if p["status"] == "OPEN"}
            held_sectors = {p.get("sector", "") for p in executor.portfolio["positions"] if p["status"] == "OPEN"}

            for key in list(candidates.keys()):
                t = candidates[key]

                # Already in portfolio
                if t["symbol"] in existing:
                    del candidates[key]
                    continue

                # Sector already held (correlation check)
                if t.get("sector", "") in held_sectors:
                    continue

                should, reason = smart.should_enter(
                    t["symbol"], t["underlying"], t["direction"]
                )

                if should:
                    # Fetch fresh price at entry time
                    quote = analyzer._get_quote(t["symbol"])
                    if not quote:
                        continue
                    price = quote.get("lastPrice", 0)
                    if price <= 0:
                        continue

                    # Recalculate qty at current price
                    sector_info  = SECTORS[t["sector"]]
                    sector_max_pct = sector_info.get("max_position_pct", config["max_position_pct"])
                    max_cost = config["equity"] * sector_max_pct
                    if t.get("single_stock"):
                        max_cost = min(max_cost, config["equity"] * 0.07)
                    adj_qty = int(max_cost / price)

                    # VIX-adjusted sizing
                    vix_quote = analyzer._get_quote("$VIX")
                    vix = vix_quote.get("lastPrice", 20) if vix_quote else 20
                    if vix > 35:
                        adj_qty = int(adj_qty * 0.50)
                        logger.info(f"  VIX {vix:.0f} > 35: halving {t['symbol']} qty ->{adj_qty}")
                    elif vix > 30:
                        adj_qty = int(adj_qty * 0.75)
                        logger.info(f"  VIX {vix:.0f} > 30: reducing {t['symbol']} qty ->{adj_qty}")

                    if adj_qty <= 0:
                        continue

                    t["price"] = price
                    t["qty"]   = adj_qty
                    t["cost"]  = round(adj_qty * price, 2)

                    logger.info(f"  ENTRY SIGNAL [{reason}]: {t['symbol']} x{adj_qty} @ ${price:.2f} = ${t['cost']:,.0f}")
                    result = executor.buy(t["symbol"], adj_qty, price, t)
                    if result["status"] == "FILLED":
                        existing.add(t["symbol"])
                        held_sectors.add(t.get("sector", ""))
                        del candidates[key]
                elif cycle % 10 == 0:
                    logger.info(f"  Pending {t['symbol']}: {reason}")

        # ── EXIT CHECKS ──
        open_positions = [p for p in executor.portfolio["positions"] if p["status"] == "OPEN"]
        for pos in open_positions:
            quote = analyzer._get_quote(pos["symbol"])
            if not quote:
                continue
            current_price = quote.get("lastPrice", 0)
            if current_price <= 0:
                continue

            # Update peak price
            if current_price > pos.get("peak_price", 0):
                pos["peak_price"] = current_price
                executor._save_portfolio()

            # Regime change exit (every 10 min)
            if cycle % 20 == 0:
                sector = pos.get("sector", "")
                if sector in SECTORS:
                    fresh     = analyzer.analyze_sector(sector, SECTORS[sector])
                    direction = pos.get("direction", "BULL")
                    if direction == "BULL" and fresh["bear_score"] > fresh["bull_score"] + 15:
                        executor.sell(pos, current_price, f"regime_flip_bear_{fresh['bear_score']}")
                        continue
                    elif direction == "BEAR" and fresh["bull_score"] > fresh["bear_score"] + 15:
                        executor.sell(pos, current_price, f"regime_flip_bull_{fresh['bull_score']}")
                        continue

            # Smart exit (time-of-day aware)
            should_exit, reason = exits.check_exit_with_timing(pos, current_price, hour_ct=hour_ct)
            if should_exit:
                executor.sell(pos, current_price, reason)

        # ── PORTFOLIO ANALYST (every 10 min) ──
        import time as _time
        if _time.time() - last_analyst_run > 600:
            last_analyst_run = _time.time()
            try:
                open_pos = [p for p in executor.portfolio["positions"] if p["status"] == "OPEN"]
                if open_pos:
                    analyst_results = portfolio_analyst.run_full_analysis(letf_positions=open_pos)
                    for ar in analyst_results:
                        if ar["action"] == "SELL":
                            logger.warning(f"ANALYST RECOMMENDS SELL: {ar['symbol']} - {', '.join(ar['flags'])}")
                            for pos in open_pos:
                                if pos["symbol"] == ar["symbol"]:
                                    quote = analyzer._get_quote(pos["symbol"])
                                    if quote:
                                        price = quote.get("lastPrice", 0)
                                        if price > 0:
                                            executor.sell(pos, price, f"analyst_{','.join(ar['flags'])}")
                                    break
            except Exception as e:
                logger.warning(f"Portfolio analyst error: {e}")

        # Status every 5 minutes
        if cycle % 10 == 0:
            s = executor.get_summary()
            logger.info(
                f"[{now.strftime('%H:%M')}] "
                f"Open:{s['open']} Pending:{len(candidates)} "
                f"Deployed:${s['deployed']:,.0f} Cash:${s['cash']:,.0f} P&L:${s['total_pnl']:+,.0f}"
            )

        cycle += 1
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if args.live:
        logger.warning("=" * 60)
        logger.warning("LIVE MODE - REAL MONEY - PCRA ACCOUNT")
        logger.warning("=" * 60)

    run(live=args.live)
