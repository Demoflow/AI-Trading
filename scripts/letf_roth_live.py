"""
LETF Swing System - Main Live Script.
Scans sectors, enters high-conviction leveraged ETF trades,
monitors positions, manages exits.
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
from aggressive.portfolio_analyst import PortfolioAnalyst
from letf.earnings_calendar import EarningsCalendar
from letf.earnings_cluster import SectorEarningsCluster


def run(live=False):
    setup_logging()
    client = get_schwab_client()
    logger.info("Schwab connected")

    config = json.load(open("config/letf_roth_config.json"))
    executor = LETFExecutor(client, live=live, config_path="config/letf_roth_config.json", portfolio_path="config/letf_roth_portfolio.json")
    exits = LETFExitManager(config)
    portfolio_analyst = PortfolioAnalyst(client)
    last_analyst_run = 0
    analyzer = SectorAnalyzer(client)
    earnings = EarningsCalendar(client)
    clusters = SectorEarningsCluster()

    logger.info("=" * 60)
    logger.info("LEVERAGED ETF SWING - DAD'S ROTH IRA")
    logger.info(f"Mode: {'LIVE' if live else 'PAPER'}")
    # Dynamic equity sync
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

    # ── SCAN ALL SECTORS ──
    # Daily loss halt check
    if live:
        bal = executor.get_real_balance()
        if bal:
            daily_change = bal["equity"] - config["equity"]
            daily_pct = daily_change / config["equity"]
            if daily_pct < -config["daily_loss_halt_pct"]:
                logger.warning(f"DAILY HALT: equity down {daily_pct:.1%} (${daily_change:+,.0f})")
                logger.warning("No new trades today. Monitoring exits only.")
                # Skip to monitoring loop without entering new trades
                # Set a flag
    daily_halt = False
    weekly_halt = False
    if live:
        if executor.check_weekly_drawdown():
            weekly_halt = True
            logger.warning("WEEKLY DRAWDOWN HALT - reducing all positions by 50%")
    if live:
        bal = executor.get_real_balance()
        if bal:
            daily_change = bal["equity"] - config["equity"]
            if daily_change < -(config["equity"] * config["daily_loss_halt_pct"]):
                daily_halt = True
                logger.warning(f"DAILY HALT ACTIVE: ${daily_change:+,.0f}")

    logger.info("Scanning sectors...")
    results = []
    for sector_name, sector_info in SECTORS.items():
        result = analyzer.analyze_sector(sector_name, sector_info)
        results.append(result)
        bull = result["bull_score"]
        bear = result["bear_score"]

        # Earnings boost check
        try:
            earn_boost, earn_dir, earn_detail = earnings.get_earnings_boost(sector_name)
            if earn_boost > 0:
                if earn_dir == "BULL":
                    bull += earn_boost
                elif earn_dir == "BEAR":
                    bear += earn_boost
                if earn_detail:
                    logger.info(f"    EARNINGS: {earn_detail['symbol']} in {earn_detail['days_until']}d "
                                f"flow={earn_dir} conv={earn_detail['flow_conviction']}")
        except Exception:
            pass

        # Earnings cluster boost
        try:
            cluster_boost, cluster_stocks = clusters.get_cluster_boost(sector_name)
            if cluster_boost > 0:
                bull += cluster_boost
                bear += cluster_boost
                names = [s["symbol"] for s in cluster_stocks]
                logger.info(f"    CLUSTER: {sector_name} {len(cluster_stocks)} earnings ({', '.join(names)}) +{cluster_boost}pts")
        except Exception:
            pass

        best = "BULL" if bull > bear else "BEAR"
        score = max(bull, bear)
        logger.info(
                f"  {sector_name:<12} {best} {score:>3} | "
                f"bull={bull} bear={bear} | "
                f"{result['signals'].get('structure', '?')} "
                f"RS={result['signals'].get('rs_vs_spy', 0):+.1f}% "
                f"Mom5d={result['signals'].get('mom_5d', 0):+.1f}%"
            )

    # ── SELECT TRADES ──
    trades = []
    for r in results:
        sector_name = r["sector"]
        sector_info = SECTORS[sector_name]

        # Single-stock LETFs have stricter rules
        sector_min_conv = sector_info.get("min_conviction", config["min_conviction"])
        sector_max_pct = sector_info.get("max_position_pct", config["max_position_pct"])
        is_single = sector_info.get("single_stock", False)

        if r["bull_score"] >= sector_min_conv:
            etf = sector_info["bull"]
            quote = analyzer._get_quote(etf)
            if quote:
                price = quote.get("lastPrice", 0)
                if price > 0:
                    max_cost = config["equity"] * sector_max_pct
                    if is_single:
                        max_cost = min(max_cost, config["equity"] * 0.07)
                    qty = int(max_cost / price)
                    if qty > 0:
                        trades.append({
                            "symbol": etf,
                            "direction": "BULL",
                            "sector": sector_name,
                            "score": r["bull_score"],
                            "price": price,
                            "qty": qty,
                            "cost": round(qty * price, 2),
                            "leverage": sector_info["leverage"],
                            "signals": r["signals"],
                        })

        if r["bear_score"] >= sector_min_conv:
            etf = sector_info["bear"]
            quote = analyzer._get_quote(etf)
            if quote:
                price = quote.get("lastPrice", 0)
                if price > 0:
                    max_cost = config["equity"] * sector_max_pct
                    qty = int(max_cost / price)
                    if qty > 0:
                        trades.append({
                            "symbol": etf,
                            "direction": "BEAR",
                            "sector": sector_name,
                            "score": r["bear_score"],
                            "price": price,
                            "qty": qty,
                            "cost": round(qty * price, 2),
                            "leverage": sector_info["leverage"],
                            "signals": r["signals"],
                        })

    # Sort by conviction
    trades.sort(key=lambda x: x["score"], reverse=True)

    logger.info(f"\nTrades found: {len(trades)}")
    for t in trades:
        logger.info(
            f"  {t['direction']:<4} {t['symbol']:<5} ({t['sector']}) "
            f"score={t['score']} ${t['cost']:,.0f} x{t['qty']}"
        )

    # ── EXECUTE ENTRIES ──
    if daily_halt or weekly_halt:
        logger.warning(f"Skipping entries - {'daily' if daily_halt else 'weekly'} halt active")
        trades = []
    existing = {p["symbol"] for p in executor.portfolio["positions"] if p["status"] == "OPEN"}
    # Also track sectors already held to prevent correlation
    held_sectors = {p.get("sector","") for p in executor.portfolio["positions"] if p["status"] == "OPEN"}
    for t in trades:
        if t["symbol"] in existing:
            continue
        # Correlation check: max 1 position per sector
        if t.get("sector","") in held_sectors:
            logger.info(f"  SKIP {t['symbol']}: sector {t['sector']} already held")
            continue
        # VIX-adjusted sizing (FIX 10)
        vix_quote = analyzer._get_quote("$VIX")
        vix = vix_quote.get("lastPrice", 20) if vix_quote else 20
        adj_qty = t["qty"]
        if vix > 30:
            adj_qty = int(t["qty"] * 0.75)
            logger.info(f"  VIX {vix:.0f} > 30: reducing {t['symbol']} qty {t['qty']}->{adj_qty}")
        elif vix > 35:
            adj_qty = int(t["qty"] * 0.50)
        result = executor.buy(t["symbol"], adj_qty, t["price"], t)
        if result["status"] == "FILLED":
            existing.add(t["symbol"])
            held_sectors.add(t.get("sector",""))

    # ── MONITOR LOOP ──
    logger.info("\nMonitoring positions...")
    cycle = 0
    while True:
        from datetime import datetime
        now = datetime.now()
        hour = now.hour + now.minute / 60.0

        # Market hours check (8:30 AM - 3:00 PM CT)
        if hour < 8.5 or hour > 15.0:
            if hour > 15.0:
                logger.info("Market closed.")
                # Sync equity to config for tomorrow's sizing
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

        # Check exits with peak tracking + regime change
        open_positions = [p for p in executor.portfolio["positions"] if p["status"] == "OPEN"]
        for pos in open_positions:
            quote = analyzer._get_quote(pos["symbol"])
            if not quote:
                continue
            current_price = quote.get("lastPrice", 0)
            if current_price <= 0:
                continue

            # FIX 1: Update peak price
            if current_price > pos.get("peak_price", 0):
                pos["peak_price"] = current_price
                executor._save_portfolio()

            # FIX 6: Regime change exit
            # Re-check the sector score to see if conviction has flipped
            if cycle % 20 == 0:  # Every 10 min
                sector = pos.get("sector", "")
                if sector in SECTORS:
                    fresh = analyzer.analyze_sector(sector, SECTORS[sector])
                    direction = pos.get("direction", "BULL")
                    if direction == "BULL" and fresh["bear_score"] > fresh["bull_score"] + 15:
                        executor.sell(pos, current_price, f"regime_flip_bear_{fresh['bear_score']}")
                        continue
                    elif direction == "BEAR" and fresh["bull_score"] > fresh["bear_score"] + 15:
                        executor.sell(pos, current_price, f"regime_flip_bull_{fresh['bull_score']}")
                        continue

            should_exit, reason = exits.check_exit(pos, current_price)
            if should_exit:
                executor.sell(pos, current_price, reason)

        # Portfolio Analyst (every 30 min)
        import time as _time
        if _time.time() - last_analyst_run > 600:  # 10 min for retirement accounts (no PDT)
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
                f"Open:{s['open']} Deployed:${s['deployed']:,.0f} "
                f"Cash:${s['cash']:,.0f} P&L:${s['total_pnl']:+,.0f}"
            )

        cycle += 1
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if args.live:
        logger.warning("=" * 60)
        logger.warning("LIVE MODE - REAL MONEY - ROTH IRA")
        logger.warning("=" * 60)

    run(live=args.live)
