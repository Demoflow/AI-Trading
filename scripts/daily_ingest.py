"""
Daily data ingestion script.
Fetches the last 5 trading days to catch corrections.
"""

import csv
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.ingestion.market_data import MarketDataIngester
from data.storage.database import get_session
from data.storage.models import DailyPrice
from loguru import logger


def daily_update():
    start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    symbols = []
    with open("config/universe.csv") as f:
        reader = csv.DictReader(f)
        symbols = [row["symbol"] for row in reader]

    logger.info(f"Daily ingest for {len(symbols)} symbols")

    ingester = MarketDataIngester()
    ingester.fetch_and_store(symbols=symbols, start=start)

    verify_completeness(symbols)

    logger.info("Daily ingest complete!")


def verify_completeness(symbols):
    with get_session() as session:
        latest_dates = {}
        for symbol in symbols:
            result = session.query(DailyPrice.date)\
                .filter(DailyPrice.symbol == symbol)\
                .order_by(DailyPrice.date.desc())\
                .first()
            if result:
                latest_dates[symbol] = result[0]

        if not latest_dates:
            logger.error("No data found in database!")
            return

        most_common_date = max(set(latest_dates.values()), key=list(latest_dates.values()).count)
        missing = [s for s, d in latest_dates.items() if d < most_common_date]

        if missing:
            logger.warning(f"Symbols missing latest data ({most_common_date}): {missing}")
        else:
            logger.info(f"All symbols current through {most_common_date}")


if __name__ == "__main__":
    daily_update()