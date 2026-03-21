"""技术指标计算。输入单只股票日线 DataFrame，返回附加指标列。"""

import pandas as pd


def ma(df, periods=(5, 10, 20, 60)):
    for p in periods:
        if len(df) >= p:
            df[f"ma{p}"] = df["close"].rolling(p).mean()
    return df


def macd(df, fast=12, slow=26, signal=9):
    ema_f = df["close"].ewm(span=fast, adjust=False).mean()
    ema_s = df["close"].ewm(span=slow, adjust=False).mean()
    df["dif"] = ema_f - ema_s
    df["dea"] = df["dif"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = (df["dif"] - df["dea"]) * 2
    return df


def atr(df, period=14):
    """ATR（Wilder smoothing）。"""
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)

    vals = pd.Series(index=df.index, dtype=float)
    if len(df) < period:
        df["atr"] = vals
        return df

    prev = tr.iloc[:period].mean()
    vals.iloc[period - 1] = prev
    for i in range(period, len(tr)):
        prev = (prev * (period - 1) + tr.iloc[i]) / period
        vals.iloc[i] = prev

    df["atr"] = vals
    return df


def amplitude(df):
    """当日振幅 (%)。"""
    df["amplitude"] = (df["high"] - df["low"]) / df["close"].shift(1) * 100
    return df


def relative_volume_daily(df, period=5):
    """日级相对成交量：当日量 / 过去N日均量。非A股分钟级"量比"。"""
    avg = df["volume"].rolling(period).mean().shift(1)
    df["rel_vol"] = df["volume"] / avg.replace(0, float("nan"))
    return df


def compute_all(df):
    """计算全部指标。"""
    df = df.copy()
    ma(df)
    macd(df)
    atr(df)
    amplitude(df)
    relative_volume_daily(df)
    return df
