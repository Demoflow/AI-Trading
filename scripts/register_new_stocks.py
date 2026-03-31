"""
Register new stocks in the database stocks table.
Run after expanding universe.csv.
"""

import os
import sys
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from data.storage.database import get_session
from data.storage.models import Stock


def register():
    with open("config/universe.csv") as f:
        reader = csv.DictReader(f)
        universe = list(reader)

    with get_session() as session:
        existing = {
            s.symbol for s in session.query(Stock).all()
        }
        added = 0
        for row in universe:
            sym = row["symbol"]
            if sym not in existing:
                stock = Stock(
                    symbol=sym,
                    sector=row.get("sector", ""),
                    market_cap_tier=row.get("market_cap_tier", "large"),
                )
                session.add(stock)
                added += 1
                logger.info(f"Registered: {sym}")
        session.commit()
    logger.info(f"Registered {added} new stocks")


if __name__ == "__main__":
    register()
