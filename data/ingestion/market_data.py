"""
Market data ingestion using yfinance.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import text
from loguru import logger
from data.storage.database import get_session
from data.storage.models import Stock, DailyPrice, DataQualityLog


class MarketDataIngester:

    def __init__(self):
        self.source = "yfinance"

    def fetch_historical(self, symbols, start, end=None):
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"Fetching {len(symbols)} symbols from {start} to {end}")

        all_data = []
        batch_size = 20

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            batch_str = " ".join(batch)

            try:
                df = yf.download(
                    batch_str,
                    start=start,
                    end=end,
                    group_by="ticker",
                    auto_adjust=False,
                    threads=True
                )

                if df.empty:
                    logger.warning(f"No data returned for batch {i//batch_size + 1}")
                    continue

                if len(batch) == 1:
                    symbol = batch[0]
                    records = df.reset_index()
                    records["symbol"] = symbol
                    records.columns = [str(c).lower().replace(" ", "_") if isinstance(c, str) else str(c[0]).lower().replace(" ", "_") for c in records.columns]
                    all_data.append(records)
                else:
                    for symbol in batch:
                        try:
                            if symbol in df.columns.get_level_values(0):
                                ticker_df = df[symbol].dropna(how="all")
                                if ticker_df.empty:
                                    continue
                                records = ticker_df.reset_index()
                                records["symbol"] = symbol
                                records.columns = [str(c).lower().replace(" ", "_") for c in records.columns]
                                all_data.append(records)
                        except (KeyError, Exception) as e:
                            logger.warning(f"Symbol {symbol} not found in batch: {e}")

            except Exception as e:
                logger.error(f"Batch fetch failed: {e}")
                for symbol in batch:
                    try:
                        single = yf.download(symbol, start=start, end=end, auto_adjust=False)
                        if not single.empty:
                            records = single.reset_index()
                            records["symbol"] = symbol
                            records.columns = [str(c).lower().replace(" ", "_") if isinstance(c, str) else str(c[0]).lower().replace(" ", "_") for c in records.columns]
                            all_data.append(records)
                    except Exception as inner_e:
                        logger.error(f"Individual fetch failed for {symbol}: {inner_e}")

            logger.info(f"Fetched batch {i//batch_size + 1}/{(len(symbols)-1)//batch_size + 1}")

        if not all_data:
            return pd.DataFrame()

        combined = pd.concat(all_data, ignore_index=True)

        # Standardize columns
        col_renames = {}
        for c in combined.columns:
            cl = str(c).lower()
            if "date" in cl or "timestamp" in cl:
                col_renames[c] = "date"
            elif cl == "adj_close" or "adj" in cl:
                col_renames[c] = "adj_close"

        combined = combined.rename(columns=col_renames)

        if "adj_close" not in combined.columns:
            combined["adj_close"] = combined.get("close", 0)

        combined["date"] = pd.to_datetime(combined["date"]).dt.date

        logger.info(f"Total records fetched: {len(combined)}")
        return combined

    def store_prices(self, df):
        if df.empty:
            return

        with get_session() as session:
            stocks = {s.symbol: s.id for s in session.query(Stock).all()}

            inserted = 0

            for _, row in df.iterrows():
                symbol = row.get("symbol", "")
                if symbol not in stocks:
                    continue

                try:
                    stmt = text("""
                        INSERT INTO daily_prices
                            (stock_id, symbol, date, open, high, low, close, adj_close, volume, data_source, ingested_at)
                        VALUES
                            (:stock_id, :symbol, :date, :open, :high, :low, :close, :adj_close, :volume, :source, :now)
                        ON CONFLICT (symbol, date)
                        DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            adj_close = EXCLUDED.adj_close,
                            volume = EXCLUDED.volume,
                            ingested_at = EXCLUDED.ingested_at
                    """)

                    session.execute(stmt, {
                        "stock_id": stocks[symbol],
                        "symbol": symbol,
                        "date": row["date"],
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "adj_close": float(row.get("adj_close", row.get("close", 0))),
                        "volume": int(row.get("volume", 0)),
                        "source": self.source,
                        "now": datetime.utcnow()
                    })
                    inserted += 1
                except Exception as e:
                    logger.warning(f"Error storing {symbol} {row.get('date')}: {e}")

            logger.info(f"Stored {inserted} price records")

    def validate(self, df):
        issues = []

        for symbol in df["symbol"].unique():
            sdf = df[df["symbol"] == symbol].sort_values("date")

            neg_mask = (sdf[["open", "high", "low", "close"]] < 0).any(axis=1)
            for _, row in sdf[neg_mask].iterrows():
                issues.append({
                    "symbol": symbol, "date": row["date"],
                    "issue_type": "negative_price",
                    "description": "Negative price detected",
                    "severity": "critical"
                })

            ohlc_bad = sdf[sdf["high"] < sdf["low"]]
            for _, row in ohlc_bad.iterrows():
                issues.append({
                    "symbol": symbol, "date": row["date"],
                    "issue_type": "ohlc_violation",
                    "description": f"High ({row['high']}) < Low ({row['low']})",
                    "severity": "error"
                })

            zero_vol = sdf[sdf["volume"] == 0]
            for _, row in zero_vol.iterrows():
                issues.append({
                    "symbol": symbol, "date": row["date"],
                    "issue_type": "zero_volume",
                    "description": "Zero volume day",
                    "severity": "warning"
                })

            if len(sdf) > 1:
                sdf_copy = sdf.copy()
                sdf_copy["pct_change"] = sdf_copy["close"].pct_change().abs()
                spikes = sdf_copy[sdf_copy["pct_change"] > 0.25].dropna(subset=["pct_change"])
                for _, row in spikes.iterrows():
                    issues.append({
                        "symbol": symbol, "date": row["date"],
                        "issue_type": "price_spike",
                        "description": f"Daily move of {row['pct_change']:.1%}",
                        "severity": "warning"
                    })

        if issues:
            logger.warning(f"Found {len(issues)} data quality issues")
        else:
            logger.info("All quality checks passed")

        return issues

    def log_quality_issues(self, issues):
        with get_session() as session:
            for issue in issues:
                log_entry = DataQualityLog(**issue, detected_at=datetime.utcnow())
                session.add(log_entry)
            logger.info(f"Logged {len(issues)} quality issues to database")

    def fetch_and_store(self, symbols, start, end=None):
        df = self.fetch_historical(symbols, start, end)
        if df.empty:
            logger.warning("No data to store")
            return

        issues = self.validate(df)
        if issues:
            self.log_quality_issues(issues)

        self.store_prices(df)