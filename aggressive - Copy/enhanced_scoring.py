"""
Enhanced Scoring Modules.
- Weekly trend confirmation
- Sector rotation weighting
- Delta-adjusted position sizing
"""

from loguru import logger


class WeeklyTrend:
    """Check weekly timeframe for trend confirmation."""

    @staticmethod
    def get_weekly_trend(stock_df):
        if stock_df is None or len(stock_df) < 50:
            return "NEUTRAL", 1.0

        # Approximate weekly by using every 5th bar
        weekly_close = stock_df["close"].iloc[::5]
        if len(weekly_close) < 10:
            return "NEUTRAL", 1.0

        sma10w = weekly_close.tail(10).mean()
        sma20w = weekly_close.tail(20).mean() if len(weekly_close) >= 20 else sma10w
        price = stock_df.iloc[-1]["close"]

        if price > sma10w > sma20w:
            return "UP", 1.10  # 10% boost
        elif price < sma10w < sma20w:
            return "DOWN", 0.85  # 15% penalty for calls
        return "NEUTRAL", 1.0


class SectorRotation:
    """Weight sectors by relative momentum."""

    SECTOR_ETFS = {
        "Technology": "XLK",
        "Financials": "XLF",
        "Healthcare": "XLV",
        "Consumer Discretionary": "XLY",
        "Energy": "XLE",
        "Industrials": "XLI",
        "Consumer Staples": "XLP",
        "Materials": "XLB",
    }

    def __init__(self):
        self.sector_scores = {}

    def calculate_rotation(self, price_data, spy_df):
        """Score each sector's relative strength."""
        if spy_df is None or len(spy_df) < 20:
            return

        spy_ret = (spy_df.iloc[-1]["close"] / spy_df.iloc[-20]["close"]) - 1

        for sector, etf in self.SECTOR_ETFS.items():
            if etf in price_data and len(price_data[etf]) >= 20:
                df = price_data[etf]
                etf_ret = (df.iloc[-1]["close"] / df.iloc[-20]["close"]) - 1
                rs = etf_ret - spy_ret
                self.sector_scores[sector] = round(rs, 4)

        if self.sector_scores:
            top = max(self.sector_scores, key=self.sector_scores.get)
            bot = min(self.sector_scores, key=self.sector_scores.get)
            logger.info(f"Sector rotation: Top={top} ({self.sector_scores[top]:+.2%}) Bot={bot} ({self.sector_scores[bot]:+.2%})")

    def get_sector_modifier(self, sector):
        """Get sizing modifier for a sector."""
        if sector not in self.sector_scores:
            return 1.0
        rs = self.sector_scores[sector]
        if rs > 0.02:
            return 1.15  # Hot sector, size up
        elif rs > 0.01:
            return 1.05
        elif rs < -0.02:
            return 0.85  # Cold sector, size down
        elif rs < -0.01:
            return 0.95
        return 1.0


class DeltaSizer:
    """Adjust position size by delta exposure."""

    @staticmethod
    def adjust_qty(qty, delta, target_delta_exposure=0.55):
        """Scale quantity so total delta exposure is consistent."""
        if delta <= 0:
            return qty
        ratio = target_delta_exposure / delta
        adjusted = max(1, round(qty * ratio))
        return adjusted
