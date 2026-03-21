"""筛选策略集合。

策略函数签名：func(code, df) -> dict | None
  code: 股票代码
  df: 已算好指标的 DataFrame（含 ma5/ma10/ma20/dif/dea/macd_hist/atr/amplitude/rel_vol）
  返回 dict 表示命中，None 表示未命中
"""

import math


def _nan(val):
    if val is None:
        return True
    try:
        return math.isnan(float(val))
    except (TypeError, ValueError):
        return True


# ---------- 止跌形态检测 ----------


def _detect_pattern(df):
    """检测最新K线止跌形态。优先级：锤子线 > 双针 > 小阳企稳 > 十字星 > 缩量横住。"""
    if len(df) < 3:
        return None

    last, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]

    o, h, l, c = (
        float(last["open"]),
        float(last["high"]),
        float(last["low"]),
        float(last["close"]),
    )
    body = abs(c - o)
    lower = min(c, o) - l
    rng = h - l

    # 锤子线：下影线/实体 >= 2.5，上影线 <= 振幅 25%
    if rng > 0 and body > 0:
        if lower >= body * 2.5 and (h - max(c, o)) <= rng * 0.25:
            return "锤子线"

    # 双针：连续2日 下影线/实体 >= 1.5
    po, pl, pc = float(prev["open"]), float(prev["low"]), float(prev["close"])
    pb = abs(pc - po)
    pl_shadow = min(pc, po) - pl
    if pb > 0 and body > 0:
        if lower >= body * 1.5 and pl_shadow >= pb * 1.5:
            return "双针探底"

    # 小阳企稳：涨跌幅 -0.5%~+1.5%，振幅 <= 前3日均振幅 * 0.8
    chg = float(last.get("change_pct", 0) or 0)
    amp = float(last.get("amplitude", 0) or 0)
    if len(df) >= 4 and "amplitude" in df.columns:
        avg_amp = df["amplitude"].iloc[-4:-1].mean()
        if -0.5 <= chg <= 1.5 and amp > 0 and avg_amp > 0 and amp <= avg_amp * 0.8:
            return "小阳企稳"

    # 十字星：实体 <= 振幅 10%
    if rng > 0 and body <= rng * 0.10:
        return "十字星"

    # 缩量横住：连续3日小实体(< 1.5%)，未创新低
    p2o, p2l, p2c = float(prev2["open"]), float(prev2["low"]), float(prev2["close"])
    p2b = abs(p2c - p2o)
    thresh = 0.015
    all_small = all(
        b / max(ref, 1.0) < thresh for b, ref in [(body, c), (pb, pc), (p2b, p2c)]
    )
    if all_small and l >= min(pl, p2l) * 0.995:
        return "缩量横住"

    return None


# ---------- 策略1：趋势回调企稳 ----------


def strategy_trend_pullback_stabilize(code, df):
    """趋势回调企稳：筛选上升趋势中缩量回调到位、基本跌不动的股票。

    硬条件（全部满足才入选）：
      1. close > MA20
      2. MA10 > MA20，MA20[t] > MA20[t-5]
      3. 近10日高点回撤 3%~8%
      4. 近3日均量 / 前5日均量 <= 0.75
      5. min(|close-MA10|, |close-MA20|) / ATR <= 0.8
      6. 近3日最低 >= 前7日最低 * 0.995
      7. 近2日累计涨跌幅 > -3.5%
      8. 止跌形态（锤子线/双针/小阳企稳/十字星/缩量横住 任一）

    软条件评分（100分满分）：
      趋势强度 35分：DIF>0 +10, 靠近MA10 +10, 回撤4%~6% +10, MA5>MA10 +5
      缩量质量 25分：缩量比越低越好 最高+15, 波动收敛 +10
      止跌形态 25分：锤子25/双针22/小阳20/十字星18/横住15
      MACD辅助 15分：DIF>DEA +8, macd_hist连续2日改善 +7
    """
    if len(df) < 30:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3] if len(df) >= 3 else prev

    # 必要列检查
    need = ("ma5", "ma10", "ma20", "close", "volume", "atr", "dif", "dea", "macd_hist")
    for col in need:
        if col not in df.columns or _nan(last.get(col)):
            return None

    close = float(last["close"])
    ma5, ma10, ma20 = float(last["ma5"]), float(last["ma10"]), float(last["ma20"])
    atr_val = float(last["atr"])
    dif, dea = float(last["dif"]), float(last["dea"])
    macd_h = float(last["macd_hist"])

    if atr_val <= 0 or float(last["volume"]) <= 0:
        return None

    # --- 硬条件 ---

    # 1. close > MA20
    if close <= ma20:
        return None

    # 2. MA10 > MA20，MA20 斜率向上（5天）
    if ma10 <= ma20:
        return None
    if len(df) < 6 or _nan(df.iloc[-6].get("ma20")):
        return None
    if ma20 <= float(df.iloc[-6]["ma20"]):
        return None

    # 3. 回撤 3%~8%
    high_10d = float(df["high"].iloc[-10:].max())
    drawdown = (high_10d - close) / high_10d
    if not (0.03 <= drawdown <= 0.08):
        return None

    # 4. 缩量：近3日均量 / 前5日均量 <= 0.75
    if len(df) < 8:
        return None
    vol_3 = df["volume"].iloc[-3:].mean()
    vol_5 = df["volume"].iloc[-8:-3].mean()
    if vol_5 <= 0:
        return None
    vol_ratio = float(vol_3 / vol_5)
    if vol_ratio > 0.75:
        return None

    # 5. 价格位置
    d10 = abs(close - ma10) / atr_val
    d20 = abs(close - ma20) / atr_val
    if min(d10, d20) > 0.8:
        return None

    # 6. 不破位
    ref_low = float(
        df["low"].iloc[-10:-3].min() if len(df) >= 10 else df["low"].iloc[:-3].min()
    )
    if float(df["low"].iloc[-3:].min()) < ref_low * 0.995:
        return None

    # 7. 近2日累计涨跌幅 > -3.5%
    chg_2d = float(last["change_pct"]) + float(prev["change_pct"])
    if chg_2d < -3.5:
        return None

    # 8. 止跌形态
    pattern = _detect_pattern(df)
    if pattern is None:
        return None

    # --- 评分 ---
    score = 0.0

    # 趋势强度 (35)
    if dif > 0:
        score += 10
    if d10 < d20:
        score += 10
    if 0.04 <= drawdown <= 0.06:
        score += 10
    if ma5 > ma10:
        score += 5

    # 缩量质量 (25)
    score += max(0, min(15, (0.75 - vol_ratio) / 0.40 * 15))
    if len(df) >= 5:
        # 波动收敛：amplitude 下降或 ATR 下降，任一即可
        vol_converge = False
        if "amplitude" in df.columns:
            amp_new = df["amplitude"].iloc[-2:].mean()
            amp_old = df["amplitude"].iloc[-5:-2].mean()
            if amp_new < amp_old:
                vol_converge = True
        atr_old = (
            float(df.iloc[-4]["atr"]) if not _nan(df.iloc[-4].get("atr")) else atr_val
        )
        if atr_val < atr_old:
            vol_converge = True
        if vol_converge:
            score += 10

    # 形态 (25)
    score += {
        "锤子线": 25,
        "双针探底": 22,
        "小阳企稳": 20,
        "十字星": 18,
        "缩量横住": 15,
    }.get(pattern, 15)

    # MACD (15)
    if dif > dea:
        score += 8
    if len(df) >= 3:
        mh = [
            float(prev2["macd_hist"]),
            float(prev["macd_hist"]),
            float(last["macd_hist"]),
        ]
        if mh[2] > mh[1] > mh[0]:
            score += 7

    return {
        "code": code,
        "name": "",
        "形态": pattern,
        "总分": round(score, 1),
        "close": round(close, 2),
        "MA10": round(ma10, 2),
        "MA20": round(ma20, 2),
        "回撤": f"{drawdown * 100:.1f}%",
        "缩量比": f"{vol_ratio:.2f}",
        "DIF": round(dif, 3),
        "DEA": round(dea, 3),
        "MACD": round(macd_h, 3),
        "ATR": round(atr_val, 2),
    }


# ---------- 占位策略 ----------


def _placeholder(code, df):
    return None


# 策略注册表：菜单序号 -> (名称, 函数)
STRATEGIES = {
    "1": ("趋势回调企稳", strategy_trend_pullback_stabilize),
    "2": ("策略2", _placeholder),
    "3": ("策略3", _placeholder),
    "4": ("策略4", _placeholder),
}
