"""
Technical feature computation.
All features use ONLY past data - no lookahead bias.
"""

import pandas as pd
import numpy as np


def add_returns(df, windows=[1, 5, 10, 21]):
    df = df.copy()
    for w in windows:
        df[f"return_{w}d"] = df["close"].pct_change(w)
        df[f"fwd_return_{w}d"] = df["close"].shift(-w) / df["close"] - 1
    return df


def add_rsi(df, period=14):
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss
    df[f"rsi_{period}"] = 100 - (100 / (1 + rs))
    return df


def add_macd(df, fast=12, slow=26, signal=9):
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()

    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_histogram"] = df["macd"] - df["macd_signal"]
    return df


def add_bollinger_bands(df, period=20, num_std=2.0):
    df = df.copy()
    df["bb_middle"] = df["close"].rolling(period).mean()
    bb_std = df["close"].rolling(period).std()
    df["bb_upper"] = df["bb_middle"] + (bb_std * num_std)
    df["bb_lower"] = df["bb_middle"] - (bb_std * num_std)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]
    df["bb_position"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    return df


def add_atr(df, period=14):
    df = df.copy()
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()

    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df[f"atr_{period}"] = true_range.ewm(span=period, adjust=False).mean()
    df[f"atr_pct_{period}"] = df[f"atr_{period}"] / df["close"]
    return df


def add_volume_features(df):
    df = df.copy()
    df["volume_sma_20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_sma_20"]
    df["volume_trend"] = df["volume"].rolling(5).mean() / df["volume"].rolling(20).mean()

    df["obv"] = (np.sign(df["close"].diff()) * df["volume"]).cumsum()
    df["obv_slope"] = df["obv"].diff(5) / 5
    return df


def add_moving_average_features(df):
    df = df.copy()
    for period in [10, 20, 50, 200]:
        ma = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std()
        df[f"ma_{period}_zscore"] = (df["close"] - ma) / std
        df[f"above_ma_{period}"] = (df["close"] > ma).astype(int)

    df["ma_50_200_ratio"] = (
        df["close"].rolling(50).mean() / df["close"].rolling(200).mean()
    )
    return df


def compute_all_features(df):
    df = add_returns(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_bollinger_bands(df)
    df = add_atr(df)
    df = add_volume_features(df)
    df = add_moving_average_features(df)
    return df