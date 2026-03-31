"""
Run the aggressive mode scanner.
Usage: python scripts/aggressive_scan.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def run():
    from utils.logging_setup import setup_logging
    setup_logging()

    try:
        from data.broker.schwab_auth import get_schwab_client
        client = get_schwab_client()
        logger.info("Schwab connected")
    except Exception as e:
        logger.error(f"Schwab required for aggressive mode: {e}")
        logger.error("Run: python scripts/authenticate_schwab.py")
        return

    eq = float(os.getenv("ACCOUNT_EQUITY", "16000"))

    from aggressive.aggressive_scanner import AggressiveScanner
    scanner = AggressiveScanner(client, eq)
    trades = scanner.run()

    if not trades:
        logger.info("No trades met criteria tonight.")
    else:
        logger.info(f"{len(trades)} trades ready for tomorrow.")
        logger.info("Market monitor will execute at entry zones.")


if __name__ == "__main__":
    run()
