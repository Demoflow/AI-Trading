"""
Master Signal Generator.
"""

import os
import sys
import csv
import pandas as pd
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data.storage.database import get_session
from data.storage.models import Stock, DailyPrice
from features.technical import compute_all_features
from analysis.scoring.composite import CompositeScorer


class SignalGenerator:

    def __init__(self, schwab_client=None):
        self.scorer = CompositeScorer()
        self.schwab_client = schwab_client

    def load_price_data(self, symbol, days=250):
        with get_session() as session:
            rows = (
                session.query(DailyPrice)
                .filter(DailyPrice.symbol == symbol)
                .order_by(DailyPrice.date.desc())
                .limit(days)
                .all()
            )
            if not rows:
                return pd.DataFrame()
            data = []
            for r in reversed(rows):
                data.append({
                    "date": r.date,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "adj_close": r.adj_close,
                    "volume": r.volume,
                })
        df = pd.DataFrame(data)
        df = compute_all_features(df)
        return df

    def get_vix(self):
        try:
            r = self.client.get_quote("$VIX")
            if r.status_code == 200:
                return r.json().get("$VIX", {}).get("quote", {}).get("lastPrice", 20)
        except Exception:
            pass
        return 20  # Fallback

    def get_options_chain(self, symbol):
        return None

    def run_full_scan(self):
        logger.info("=" * 60)
        logger.info("STARTING FULL UNIVERSE SCAN")
        logger.info("=" * 60)
        universe = []
        with open("config/universe.csv") as f:
            reader = csv.DictReader(f)
            universe = list(reader)
        tradeable = [s for s in universe if s["market_cap_tier"] in ("mega", "large")]
        logger.info(f"Scanning {len(tradeable)} tradeable stocks")
        spy_df = self.load_price_data("SPY")
        vix = self.get_vix()
        logger.info(f"VIX: {vix}")
        sector_etfs = {"Technology": "XLK", "Healthcare": "XLV", "Financials": "XLF", "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI", "Materials": "XLB"}
        sector_dfs = {}
        for sector, etf in sector_etfs.items():
            sector_dfs[sector] = self.load_price_data(etf)
        all_results = []
        for stock in tradeable:
            symbol = stock["symbol"]
            sector = stock.get("sector", "Technology")
            try:
                stock_df = self.load_price_data(symbol)
                if stock_df.empty or len(stock_df) < 200:
                    continue
                sector_df = sector_dfs.get(sector)
                chain_data = self.get_options_chain(symbol)
                bull = self.scorer.score_stock(symbol, stock_df, spy_df, sector_df, vix, chain_data, sector)
                bull["direction"] = "BULLISH"
                bull["sector"] = sector
                all_results.append(bull)
                bear = self.scorer.score_for_puts(symbol, stock_df, spy_df, sector_df, vix, chain_data, sector)
                bear["direction"] = "BEARISH"
                bear["sector"] = sector
                all_results.append(bear)
            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")
                continue
        all_results.sort(key=lambda x: x["composite_score"], reverse=True)
        actionable = [r for r in all_results if r["action"] in ("ENTER", "ENTER_REDUCED")]
        watchlist = [r for r in all_results if r["action"] == "WATCHLIST"]
        logger.info(f"Scan complete: {len(actionable)} actionable, {len(watchlist)} watchlist")
        for r in actionable[:10]:
            logger.info(f"  {r['direction']:7s} {r['symbol']:5s} Score: {r['composite_score']:5.1f} Action: {r['action']} Entry: ${r['trade_params']['entry_price']:.2f} Stop: ${r['trade_params']['stop_loss']:.2f}")
        return all_results
