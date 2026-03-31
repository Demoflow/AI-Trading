"""
#11 - Multi-Timeframe Weekly Trend Confirmation.
Checks if daily signals align with the weekly trend.
"""

import pandas as pd
import numpy as np
from loguru import logger


class WeeklyTrend:

    def analyze(self, daily_df):
        """
        Resample daily data to weekly and check trend.
        Returns modifier: 1.2 (aligned), 1.0 (neutral),
        0.7 (fighting weekly trend).
        """
        if len(daily_df) < 60:
            return 1.0, "insufficient_data"

        df = daily_df.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

        weekly = df["close"].resample("W").last().dropna()
        if len(weekly) < 10:
            return 1.0, "insufficient_weekly"

        # Weekly RSI
        delta = weekly.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 0.001)
        w_rsi = 100 - (100 / (1 + rs))
        current_w_rsi = w_rsi.iloc[-1]

        # Weekly MACD
        ema12 = weekly.ewm(span=12).mean()
        ema26 = weekly.ewm(span=26).mean()
        w_macd = ema12 - ema26
        w_signal = w_macd.ewm(span=9).mean()
        w_hist = w_macd - w_signal
        current_w_hist = w_hist.iloc[-1]

        # Weekly MA alignment
        w_sma10 = weekly.rolling(10).mean().iloc[-1]
        w_sma20 = weekly.rolling(20).mean().iloc[-1]
        w_price = weekly.iloc[-1]

        bullish_count = 0
        bearish_count = 0

        if current_w_rsi > 50:
            bullish_count += 1
        elif current_w_rsi < 45:
            bearish_count += 1

        if current_w_hist > 0:
            bullish_count += 1
        elif current_w_hist < 0:
            bearish_count += 1

        if w_price > w_sma10 > w_sma20:
            bullish_count += 1
        elif w_price < w_sma10 < w_sma20:
            bearish_count += 1

        if bullish_count >= 2:
            return 1.2, "weekly_bullish"
        elif bearish_count >= 2:
            return 0.7, "weekly_bearish"
        else:
            return 1.0, "weekly_neutral"
