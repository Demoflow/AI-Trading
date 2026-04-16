"""
Master Scheduler - Runs both Aggressive and LETF Roth systems.
"""

import os
import sys
import time
import threading
from datetime import datetime, date, timedelta

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
        import csv
        ingester = MarketDataIngester()
        today = date.today().strftime("%Y-%m-%d")
        start = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        symbols = []
        with open("config/universe.csv") as f:
            reader = csv.DictReader(f)
            symbols = [r["symbol"] for r in reader]
        ingester.fetch_and_store(symbols=symbols, start=start, end=today)
    except Exception as e:
        logger.error(f"Ingestion: {e}")

    # Aggressive scan
    logger.info("Step 2: Aggressive scan...")
    try:
        from scripts.aggressive_scan import run as agg_run
        agg_run()
    except Exception as e:
        logger.error(f"Scan: {e}")

    # Conservative scan
    logger.info("Step 3: Conservative scan...")
    try:
        from scripts.daily_scan import run_scan
        run_scan()
    except Exception as e:
        logger.error(f"Scan: {e}")

    logger.info("=" * 60)
    logger.info("EVENING WORKFLOW COMPLETE")
    logger.info("=" * 60)


def run_morning(live=False):
    from utils.logging_setup import setup_logging
    setup_logging()
    from utils.market_calendar import MarketCalendar
    cal = MarketCalendar()
    if not cal.is_market_open_today():
        logger.info("Market closed today.")
        return

    mode = "LIVE" if live else "PAPER"
    logger.info("=" * 60)
    logger.info(f"MORNING WORKFLOW — {mode}")
    logger.info("Launching: Aggressive + LETF Roth + LETF PCRA")
    logger.info("=" * 60)

    from scripts.aggressive_live import run as agg_live
    from scripts.letf_roth_live import run as roth_live
    from scripts.letf_live import run as pcra_live

    errors = []

    def run_aggressive():
        try:
            agg_live(paper=not live)
        except Exception as e:
            errors.append(f"Aggressive error: {e}")
            logger.error(f"Aggressive system crashed: {e}")

    def run_roth():
        try:
            roth_live(live=live)
        except Exception as e:
            errors.append(f"Roth error: {e}")
            logger.error(f"LETF Roth system crashed: {e}")

    def run_pcra():
        try:
            pcra_live(live=live)
        except Exception as e:
            errors.append(f"PCRA error: {e}")
            logger.error(f"LETF PCRA system crashed: {e}")

    # ── SYSTEM DISABLE GUARD ──────────────────────────────────────────
    # LETF (Roth + PCRA) and Elite V.7 (Aggressive) are paused.
    # Remove these lines to re-enable.
    logger.warning("=" * 60)
    logger.warning("MASTER SCHEDULER: LETF + Elite V.7 are DISABLED.")
    logger.warning("Only the scalper runs. Re-enable manually.")
    logger.warning("=" * 60)
    # ─────────────────────────────────────────────────────────────────

    t_agg = threading.Thread(target=run_aggressive, name="Aggressive", daemon=True)

    t_agg.start()
    logger.info("Aggressive system started.")

    t_agg.join()

    logger.info("=" * 60)
    logger.info("MORNING WORKFLOW COMPLETE")
    if errors:
        for err in errors:
            logger.error(err)
    logger.info("=" * 60)


def run_full(live=False):
    """Evening scan, wait for market open, then run morning."""
    run_evening()

    # Wait until 9:25 AM
    logger.info("Waiting for market open (9:25 AM)...")
    while True:
        now = datetime.now()
        target = now.replace(hour=9, minute=25, second=0, microsecond=0)
        if now >= target and now.hour < 16:
            break
        if now.hour >= 16:
            logger.info("Past market hours. Exiting.")
            return
        remaining = (target - now).total_seconds()
        logger.info(f"  Sleeping {remaining/60:.0f}min until 9:25 AM...")
        time.sleep(min(remaining, 300))

    run_morning(live=live)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=["evening", "morning", "full"],
        default="evening",
        nargs="?",
    )
    parser.add_argument("--live", action="store_true", help="Trade with real money")
    args = parser.parse_args()

    if args.mode == "evening":
        run_evening()
    elif args.mode == "morning":
        run_morning(live=args.live)
    elif args.mode == "full":
        run_full(live=args.live)
