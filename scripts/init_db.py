"""Run once to initialize the database and load the universe."""

import csv
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.storage.database import init_db, get_session
from data.storage.models import Stock
from loguru import logger


def load_universe(csv_path="config/universe.csv"):
    with get_session() as session:
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                existing = session.query(Stock).filter_by(symbol=row["symbol"]).first()
                if not existing:
                    stock = Stock(
                        symbol=row["symbol"],
                        name=row["name"],
                        sector=row["sector"],
                        industry=row["industry"],
                        market_cap_tier=row["market_cap_tier"]
                    )
                    session.add(stock)
                    count += 1
        logger.info(f"Loaded {count} new stocks into universe")


if __name__ == "__main__":
    logger.info("Initializing database...")
    init_db()
    logger.info("Loading stock universe...")
    load_universe()
    logger.info("Phase 1 setup complete!")
    