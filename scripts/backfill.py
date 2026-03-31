"""
One-time historical data backfill.
Loads 5 years of daily data for your entire universe.
"""

import csv
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.ingestion.market_data import MarketDataIngester
from loguru import logger


def backfill(years=5):
    end = datetime.now()
    start = end - timedelta(days=years * 365)

    symbols = []
    with open("config/universe.csv") as f:
        reader = csv.DictReader(f)
        symbols = [row["symbol"] for row in reader]

    logger.info(f"Backfilling {len(symbols)} symbols, {years} years of data")

    ingester = MarketDataIngester()
    ingester.fetch_and_store(
        symbols=symbols,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d")
    )

    logger.info("Backfill complete!")


if __name__ == "__main__":
    backfill()