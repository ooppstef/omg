"""技术指标计算层：输入日线 DataFrame，输出附加指标列的 DataFrame。

所有函数接收单只股票的 DataFrame（含 open/high/low/close/volume/amount 等列），
返回同一 DataFrame 并新增指标列。可链式调用。
"""

import pandas as pd


def ma(df, periods=(5, 10, 20, 60)):
    """移动平均线。"""
    for p in periods:
        if len(df) >= p:
            df[f"ma{p}"] = df["close"].rolling(p).mean()
    return df


def macd(df, fast=12, slow=26, signal=9):
    """MACD 指标：dif, dea, macd_hist。"""
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    df["dif"] = ema_fast - ema_slow
    df["dea"] = df["dif"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = (df["dif"] - df["dea"]) * 2
    return df


def relative_volume_daily(df, period=5):
    """日级相对成交量：当日成交量 / 过去 N 日平均日成交量。

    注意：这不是 A 股软件中的分钟级"量比"，而是日线级别的相对放量指标。
    """
    avg_vol = df["volume"].rolling(period).mean().shift(1)
    df["rel_vol"] = df["volume"] / avg_vol.replace(0, float("nan"))
    return df


def atr(df, period=14):
    """ATR 平均真实波幅（Wilder smoothing）。"""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_series = pd.Series(index=df.index, dtype=float)
    if len(df) < period:
        df["atr"] = atr_series
        return df

    prev_atr = tr.iloc[:period].mean()
    atr_series.iloc[period - 1] = prev_atr

    for i in range(period, len(tr)):
        prev_atr = ((prev_atr * (period - 1)) + tr.iloc[i]) / period
        atr_series.iloc[i] = prev_atr

    df["atr"] = atr_series
    return df


def amplitude(df):
    """当日振幅 (%)。"""
    df["amplitude"] = (df["high"] - df["low"]) / df["close"].shift(1) * 100
    return df


def compute_all(df):
    """一次性计算全部常用指标。"""
    df = df.copy()
    df = ma(df)
    df = macd(df)
    df = relative_volume_daily(df)
    df = atr(df)
    df = amplitude(df)
    return df
