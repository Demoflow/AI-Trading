"""
Evening scan with all enhancements integrated.
"""

import os
import sys
import json

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def run_scan():
    from utils.logging_setup import setup_logging
    setup_logging()

    from analysis.signals.signal_generator import SignalGenerator
    from analysis.signals.watchlist import WatchlistGenerator
    from utils.earnings_calendar import EarningsCalendar
    from utils.weekly_trend import WeeklyTrend
    from utils.sector_rotation import SectorRotation
    from utils.adaptive_threshold import AdaptiveThreshold
    from utils.enhanced_scan_saver import save_enhanced_scan

    schwab_client = None
    try:
        from data.broker.schwab_auth import get_schwab_client
        schwab_client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.warning(f"No Schwab: {e}")

    # Refresh earnings calendar weekly
    ecal = EarningsCalendar()
    if not ecal.calendar:
        try:
            ecal.refresh()
        except Exception as e:
            logger.warning(f"Earnings refresh skip: {e}")

    gen = SignalGenerator(schwab_client=schwab_client)
    scan_results = gen.run_full_scan()

    # Apply weekly trend modifier (#11)
    wt = WeeklyTrend()
    for r in scan_results:
        sym = r["symbol"]
        if r.get("direction") == "BULLISH":
            try:
                df = gen.load_price_data(sym)
                mod, label = wt.analyze(df)
                old = r["composite_score"]
                r["composite_score"] = round(old * mod, 1)
                r["weekly_trend"] = label
            except Exception:
                r["weekly_trend"] = "unknown"

    # Apply sector rotation modifier (#12)
    sr = SectorRotation()
    spy_df = gen.load_price_data("SPY")
    etf_map = {
        "Technology": "XLK", "Healthcare": "XLV",
        "Financials": "XLF",
        "Consumer Discretionary": "XLY",
        "Consumer Staples": "XLP", "Energy": "XLE",
        "Industrials": "XLI", "Materials": "XLB",
    }
    sector_dfs = {}
    for sec, etf in etf_map.items():
        sector_dfs[sec] = gen.load_price_data(etf)
    rotation = sr.rank_sectors(sector_dfs, spy_df)
    for r in scan_results:
        sec = r.get("sector", "")
        if sec in rotation:
            mod = rotation[sec]["modifier"]
            old = r["composite_score"]
            r["composite_score"] = round(old * mod, 1)
            r["sector_rotation"] = rotation[sec]["label"]

    # Apply earnings block (#5)
    for r in scan_results:
        sym = r["symbol"]
        if ecal.is_near_earnings(sym, 5):
            r["composite_score"] = min(
                r["composite_score"], 40
            )
            r["override"] = f"Earnings in {ecal.days_to_earnings(sym)}d"

    # Re-sort after modifiers
    scan_results.sort(
        key=lambda x: x["composite_score"],
        reverse=True,
    )

    # Use adaptive threshold (#14)
    at = AdaptiveThreshold()
    threshold = at.threshold
    for r in scan_results:
        score = r["composite_score"]
        if score >= threshold:
            r["action"] = "ENTER"
            r["size_modifier"] = 1.0
        elif score >= threshold - 10:
            r["action"] = "ENTER_REDUCED"
            r["size_modifier"] = 0.5
        elif score >= threshold - 20:
            r["action"] = "WATCHLIST"
            r["size_modifier"] = 0
        else:
            r["action"] = "SKIP"
            r["size_modifier"] = 0

    # Generate watchlist
    eq = float(os.getenv("ACCOUNT_EQUITY", "8000"))
    wl_gen = WatchlistGenerator()
    watchlist = wl_gen.generate(scan_results, eq)
    wl_gen.save_watchlist(watchlist)
    wl_gen.print_watchlist(watchlist)

    # Save scan history
    scan_date = datetime.now().strftime("%Y-%m-%d")
    os.makedirs("config/scan_history", exist_ok=True)
    hp = f"config/scan_history/{scan_date}.json"
    wl_gen.save_watchlist(watchlist, hp)

    # Save enhanced scan for ML (#18)
    save_enhanced_scan(scan_results)

    # Log earnings warnings
    upcoming = ecal.get_reporting_this_week()
    if upcoming:
        logger.info("Earnings this week:")
        for sym, ed, days in upcoming:
            logger.info(f"  {sym}: {ed} ({days}d)")

    # Log sector rotation
    if rotation:
        leaders = [
            f"{s}({d['rs']:+.1%})"
            for s, d in rotation.items()
            if d["label"] == "LEADING"
        ]
        laggers = [
            f"{s}({d['rs']:+.1%})"
            for s, d in rotation.items()
            if d["label"] == "LAGGING"
        ]
        if leaders:
            logger.info(f"Leading sectors: {', '.join(leaders)}")
        if laggers:
            logger.info(f"Lagging sectors: {', '.join(laggers)}")

    logger.info(
        f"Scan complete at "
        f"{datetime.now().strftime('%H:%M:%S')} "
        f"(threshold: {threshold})"
    )


if __name__ == "__main__":
    run_scan()
