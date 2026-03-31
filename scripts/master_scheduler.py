"""
Master Scheduler - Updated with token keepalive.
"""

import os
import sys
import time
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def run_evening():
    from utils.logging_setup import setup_logging
    setup_logging()
    from utils.market_calendar import MarketCalendar
    cal = MarketCalendar()
    if not cal.is_market_open_today():
        logger.info("Market was closed today. Skipping.")
        return

    logger.info("=" * 60)
    logger.info("EVENING WORKFLOW")
    logger.info("=" * 60)

    # Token keepalive
    logger.info("Step 0: Token keepalive...")
    try:
        from scripts.token_keepalive import keepalive
        keepalive()
    except Exception as e:
        logger.warning(f"Keepalive: {e}")

    # Backfill today's data
    logger.info("Step 1: Updating price data...")
    try:
        from data.ingestion.market_data import MarketDataIngester
        from datetime import timedelta
        import csv
        ingester = MarketDataIngester()
        today = date.today().strftime("%Y-%m-%d")
        start = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        symbols = []
        with open("config/universe.csv") as f:
            reader = csv.DictReader(f)
            symbols = [r["symbol"] for r in reader]
        ingester.fetch_and_store(
            symbols=symbols, start=start, end=today,
        )
    except Exception as e:
        logger.error(f"Ingestion: {e}")

    # Aggressive scan
    logger.info("Step 2: Aggressive scan...")
    try:
        from scripts.aggressive_scan import run as agg_run
        agg_run()
    except Exception as e:
        logger.error(f"Scan: {e}")

    # Also run conservative scan
    logger.info("Step 3: Conservative scan...")
    try:
        from scripts.daily_scan import run_scan
        run_scan()
    except Exception as e:
        logger.error(f"Scan: {e}")

    logger.info("=" * 60)
    logger.info("EVENING WORKFLOW COMPLETE")
    logger.info("=" * 60)


def run_morning():
    from utils.logging_setup import setup_logging
    setup_logging()
    from utils.market_calendar import MarketCalendar
    cal = MarketCalendar()
    if not cal.is_market_open_today():
        logger.info("Market closed today.")
        return

    logger.info("Starting aggressive monitor...")
    from scripts.aggressive_live import run as agg_live
    agg_live(paper=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=["evening", "morning", "full"],
        default="evening",
        nargs="?",
    )
    args = parser.parse_args()

    if args.mode == "evening":
        run_evening()
    elif args.mode == "morning":
        run_morning()
    elif args.mode == "full":
        run_evening()
