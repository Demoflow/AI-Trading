"""
Smoke test: run after backfill to confirm data integrity.
"""

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.storage.database import get_session
from data.storage.models import Stock, DailyPrice, DataQualityLog
from features.technical import compute_all_features
from loguru import logger


def validate():
    with get_session() as session:
        stock_count = session.query(Stock).count()
        logger.info(f"Stocks in universe: {stock_count}")
        assert stock_count >= 40, "Universe too small"

        price_count = session.query(DailyPrice).count()
        logger.info(f"Total price records: {price_count:,}")
        assert price_count > 10000, "Too few price records"

        min_date = session.query(DailyPrice.date).order_by(DailyPrice.date.asc()).first()[0]
        max_date = session.query(DailyPrice.date).order_by(DailyPrice.date.desc()).first()[0]
        logger.info(f"Date range: {min_date} to {max_date}")

        test_symbol = "AAPL"
        rows = session.query(DailyPrice)\
            .filter(DailyPrice.symbol == test_symbol)\
            .order_by(DailyPrice.date)\
            .all()

        df = pd.DataFrame([{
            "date": r.date, "open": r.open, "high": r.high,
            "low": r.low, "close": r.close, "adj_close": r.adj_close,
            "volume": r.volume
        } for r in rows])

        logger.info(f"{test_symbol}: {len(df)} trading days loaded")

        featured = compute_all_features(df)
        feature_cols = [c for c in featured.columns if c not in
                       ["date", "open", "high", "low", "close", "adj_close", "volume"]]
        logger.info(f"Features computed: {len(feature_cols)} columns")

        valid_rows = featured.dropna()
        logger.info(f"Valid rows after warmup: {len(valid_rows)} / {len(featured)}")

        quality_count = session.query(DataQualityLog).count()
        logger.info(f"Quality issues logged: {quality_count}")

    logger.info("=== ALL VALIDATION CHECKS PASSED ===")


if __name__ == "__main__":
    validate()