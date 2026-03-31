"""
Sector Earnings Cluster Detector.
Identifies weeks where multiple sector-moving stocks report earnings.
These clusters amplify sector ETF moves by 2-3x normal.
"""
from datetime import date, timedelta
from loguru import logger


# Approximate 2026 earnings calendar for mega-caps
# Format: "YYYY-MM-DD" (earnings date)
# Updated quarterly - these are Q1 2026 estimates
EARNINGS_CALENDAR_2026 = {
    # Tech earnings week (late Jan / late Apr / late Jul / late Oct)
    "AAPL": ["2026-01-29", "2026-04-30", "2026-07-30", "2026-10-29"],
    "MSFT": ["2026-01-28", "2026-04-29", "2026-07-29", "2026-10-28"],
    "GOOGL": ["2026-02-04", "2026-04-29", "2026-07-28", "2026-10-27"],
    "META": ["2026-02-05", "2026-04-30", "2026-07-29", "2026-10-28"],
    "AMZN": ["2026-02-06", "2026-05-01", "2026-07-31", "2026-10-30"],
    "NFLX": ["2026-01-21", "2026-04-17", "2026-07-17", "2026-10-16"],
    "TSLA": ["2026-01-29", "2026-04-22", "2026-07-22", "2026-10-21"],
    # Semis
    "NVDA": ["2026-02-26", "2026-05-28", "2026-08-27", "2026-11-19"],
    "AMD": ["2026-02-04", "2026-05-06", "2026-08-05", "2026-11-04"],
    "AVGO": ["2026-03-06", "2026-06-12", "2026-09-11", "2026-12-11"],
    "MU": ["2026-03-25", "2026-06-25", "2026-09-24", "2026-12-17"],
    # Banks
    "JPM": ["2026-01-15", "2026-04-14", "2026-07-14", "2026-10-13"],
    "BAC": ["2026-01-16", "2026-04-15", "2026-07-15", "2026-10-14"],
    "GS": ["2026-01-16", "2026-04-15", "2026-07-15", "2026-10-14"],
    "MS": ["2026-01-16", "2026-04-15", "2026-07-15", "2026-10-14"],
    "WFC": ["2026-01-15", "2026-04-14", "2026-07-14", "2026-10-13"],
    # Energy
    "XOM": ["2026-01-31", "2026-05-01", "2026-07-31", "2026-10-30"],
    "CVX": ["2026-01-31", "2026-05-02", "2026-08-01", "2026-10-31"],
    # Biotech
    "AMGN": ["2026-02-04", "2026-04-28", "2026-07-28", "2026-10-27"],
    "GILD": ["2026-02-05", "2026-04-29", "2026-07-29", "2026-10-28"],
}

# Map stocks to LETF sectors
STOCK_TO_SECTOR = {
    "AAPL": "nasdaq", "MSFT": "nasdaq", "GOOGL": "nasdaq",
    "META": "fang", "AMZN": "fang", "NFLX": "fang", "TSLA": "tesla",
    "NVDA": "semis", "AMD": "semis", "AVGO": "semis", "MU": "semis",
    "JPM": "financials", "BAC": "financials", "GS": "financials",
    "MS": "financials", "WFC": "financials",
    "XOM": "energy", "CVX": "energy",
    "AMGN": "biotech", "GILD": "biotech",
}


class SectorEarningsCluster:

    def __init__(self):
        pass

    def detect_clusters(self, lookahead_days=7):
        """
        Detect sectors with 3+ earnings in the next N days.
        Returns: dict of {sector: [symbols reporting]}
        """
        today = date.today()
        cutoff = today + timedelta(days=lookahead_days)

        sector_earnings = {}

        for symbol, dates in EARNINGS_CALENDAR_2026.items():
            sector = STOCK_TO_SECTOR.get(symbol)
            if not sector:
                continue

            for d in dates:
                try:
                    earn_date = date.fromisoformat(d)
                except ValueError:
                    continue
                if today <= earn_date <= cutoff:
                    if sector not in sector_earnings:
                        sector_earnings[sector] = []
                    sector_earnings[sector].append({
                        "symbol": symbol,
                        "date": d,
                        "days_until": (earn_date - today).days,
                    })

        # Filter to clusters (2+ stocks)
        clusters = {}
        for sector, stocks in sector_earnings.items():
            if len(stocks) >= 2:
                clusters[sector] = stocks

        return clusters

    def get_cluster_boost(self, sector_name, lookahead=7):
        """
        Returns conviction boost if sector has an earnings cluster.
        2 stocks = +8 boost
        3+ stocks = +15 boost
        """
        clusters = self.detect_clusters(lookahead)

        if sector_name not in clusters:
            return 0, []

        stocks = clusters[sector_name]
        count = len(stocks)

        if count >= 3:
            boost = 15
        elif count >= 2:
            boost = 8
        else:
            boost = 0

        return boost, stocks
