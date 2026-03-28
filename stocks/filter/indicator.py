"""技术指标计算。基于 TA-Lib，输入单只股票日线 DataFrame，返回附加指标列。"""

import talib
import numpy as np


def compute_all(df):
    """计算全部指标，列名与原实现保持一致。"""
    df = df.copy()
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    v = df["volume"].values.astype(float)

    if "change_pct" not in df.columns:
        prev_c = np.roll(c, 1)
        prev_c[0] = np.nan
        df["change_pct"] = (c / prev_c - 1.0) * 100

    # MA: 简单移动平均线，N日收盘价算术平均，反映中短期趋势方向
    for p in (5, 10, 20, 60):
        if len(df) >= p:
            df[f"ma{p}"] = talib.SMA(c, timeperiod=p)

    # MACD: 指数平滑异同移动平均线
    #   DIF = EMA(12) - EMA(26)，快慢均线差值，反映短期动量变化
    #   DEA = DIF 的 9 日 EMA，DIF 的平滑信号线
    #   MACD柱 = (DIF - DEA) * 2，国内惯例乘2放大，柱状由负转正为金叉信号
    df["dif"], df["dea"], hist = talib.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)
    df["macd_hist"] = hist * 2

    # ATR: 平均真实波幅 (Average True Range)
    # 衡量价格波动剧烈程度，值越大波动越大；常用于止损位设定和仓位管理
    # 真实波幅 TR = max(高-低, |高-昨收|, |低-昨收|)，ATR = TR 的 N 日平滑
    df["atr"] = talib.ATR(h, l, c, timeperiod=14)
    df["natr"] = talib.NATR(h, l, c, timeperiod=14)

    # 振幅 (%)
    prev_c = np.roll(c, 1)
    prev_c[0] = np.nan
    df["amplitude"] = (h - l) / prev_c * 100

    # 日级相对成交量
    avg = talib.SMA(v, timeperiod=5)
    avg_shifted = np.roll(avg, 1)
    avg_shifted[0] = np.nan
    df["rel_vol"] = v / np.where(avg_shifted == 0, np.nan, avg_shifted)

    # 趋势 / 动量状态
    df["adx"] = talib.ADX(h, l, c, timeperiod=14)
    df["plus_di"] = talib.PLUS_DI(h, l, c, timeperiod=14)
    df["minus_di"] = talib.MINUS_DI(h, l, c, timeperiod=14)
    df["ppo"] = talib.PPO(c, fastperiod=12, slowperiod=26, matype=0)
    df["ppo_signal"] = talib.EMA(df["ppo"].values.astype(float), timeperiod=9)
    df["ppo_hist"] = df["ppo"] - df["ppo_signal"]

    # 量能 / 资金流状态
    df["mfi"] = talib.MFI(h, l, c, v, timeperiod=14)
    df["obv"] = talib.OBV(c, v)
    df["ad"] = talib.AD(h, l, c, v)
    df["adosc"] = talib.ADOSC(h, l, c, v, fastperiod=3, slowperiod=10)
    df["vol_ma20"] = talib.SMA(v, timeperiod=20)
    vol_ma20_shifted = np.roll(df["vol_ma20"].values.astype(float), 1)
    vol_ma20_shifted[0] = np.nan
    df["rv20"] = v / np.where(vol_ma20_shifted == 0, np.nan, vol_ma20_shifted)

    # 收盘在日内区间的位置，辅助区分吸筹 / 派发
    spread = h - l
    df["close_loc"] = (c - l) / np.where(spread == 0, np.nan, spread)

    return df
