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


CLEAR_UPTREND_RULES = {
    "window": 20,
    "lookback": 15,
    "advance_min": 0.15,
    "peak_spread_min": 0.10,
    "closes_above_ma20_min": 5,
    "ma10_above_ma20_min": 5,
    "breakaway_close_ratio": 1.03,
    "breakaway_closes_min": 4,
    "breakout_day_change_pct": 8.0,
    "breakout_days_min": 1,
}

TREND_PULLBACK_RULES = {
    "close_vs_ma20_min": 0.965,
    "ma10_vs_ma20_min": 0.995,
    "drawdown_min": 0.03,
    "drawdown_max": 0.18,
    "vol_ratio_max": 1.35,
    "distance_atr_max": 1.6,
    "support_line_ratio": 0.985,
    "chg_2d_min": -8.0,
}


def get_trend_pullback_review_context():
    """返回当前趋势回调企稳策略的关键阈值，供验证脚本复用。"""
    return {
        "close_vs_ma20_min": TREND_PULLBACK_RULES["close_vs_ma20_min"],
        "ma10_vs_ma20_min": TREND_PULLBACK_RULES["ma10_vs_ma20_min"],
        "drawdown_min": TREND_PULLBACK_RULES["drawdown_min"],
        "drawdown_max": TREND_PULLBACK_RULES["drawdown_max"],
        "drawdown_range": (
            f"{TREND_PULLBACK_RULES['drawdown_min'] * 100:.1f}%~"
            f"{TREND_PULLBACK_RULES['drawdown_max'] * 100:.1f}%"
        ),
        "volume_ratio_max": TREND_PULLBACK_RULES["vol_ratio_max"],
        "distance_atr_max": TREND_PULLBACK_RULES["distance_atr_max"],
        "support_line_ratio": TREND_PULLBACK_RULES["support_line_ratio"],
        "chg_2d_min": TREND_PULLBACK_RULES["chg_2d_min"],
        "clear_uptrend": CLEAR_UPTREND_RULES.copy(),
        "requires_signal": True,
    }


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


def _detect_stabilization_signal(df):
    """检测更宽松的企稳信号，优先返回严格形态。"""
    strict = _detect_pattern(df)
    if strict:
        return strict

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
    rng = h - l
    prev_close = float(prev["close"])
    prev_low = float(prev["low"])
    prev2_low = float(prev2["low"])
    lower = min(c, o) - l
    chg = float(last.get("change_pct", 0) or 0)
    prev_chg = float(prev.get("change_pct", 0) or 0)
    prev2_chg = float(prev2.get("change_pct", 0) or 0)

    # 长下影修复：下影明显长于实体，且尾盘没有失控走弱。
    if rng > 0 and body > 0 and lower >= max(body * 1.8, rng * 0.35) and chg >= -1.5:
        return "长下影修复"

    # 跌速收敛：前一日杀跌更重，当日跌幅明显收窄。
    if prev_chg < 0 and chg >= prev_chg * 0.7 and chg > prev_chg and prev_chg <= prev2_chg:
        return "跌速收敛"

    # 低点企稳：最近不再创新低，且实体收窄。
    if l >= min(prev_low, prev2_low) * 0.995 and body / max(c, 1.0) <= 0.015:
        return "低点企稳"

    # 小实体止跌：实体小，跌幅温和。
    if rng > 0 and body / max(c, 1.0) <= 0.012 and chg >= -1.2:
        return "小实体止跌"

    return None


def _has_clear_uptrend(df):
    """判断回调前是否存在清晰上升段。"""
    if len(df) < 25:
        return False

    window = df.iloc[-CLEAR_UPTREND_RULES["window"] :]
    peak_idx = window["high"].idxmax()
    peak = df.loc[peak_idx]

    if _nan(peak.get("ma10")) or _nan(peak.get("ma20")):
        return False

    peak_high = float(peak["high"])
    peak_ma20 = float(peak["ma20"])
    if peak_ma20 <= 0:
        return False

    start_idx = max(0, peak_idx - CLEAR_UPTREND_RULES["lookback"])
    seg = df.iloc[start_idx : peak_idx + 1]
    rally_low = float(seg["low"].min())
    if rally_low <= 0:
        return False

    advance = (peak_high - rally_low) / rally_low
    peak_spread = peak_high / peak_ma20 - 1.0
    closes_above_ma20 = (seg["close"] > seg["ma20"]).sum()
    ma10_above_ma20 = (seg["ma10"] > seg["ma20"]).sum()
    closes_break_away = (
        seg["close"] >= seg["ma20"] * CLEAR_UPTREND_RULES["breakaway_close_ratio"]
    ).sum()
    breakout_days = (
        seg["change_pct"] >= CLEAR_UPTREND_RULES["breakout_day_change_pct"]
    ).sum()

    return (
        advance >= CLEAR_UPTREND_RULES["advance_min"]
        and peak_spread >= CLEAR_UPTREND_RULES["peak_spread_min"]
        and float(peak["ma10"]) > peak_ma20
        and closes_above_ma20 >= CLEAR_UPTREND_RULES["closes_above_ma20_min"]
        and ma10_above_ma20 >= CLEAR_UPTREND_RULES["ma10_above_ma20_min"]
        and (
            closes_break_away >= CLEAR_UPTREND_RULES["breakaway_closes_min"]
            or breakout_days >= CLEAR_UPTREND_RULES["breakout_days_min"]
        )
    )


# ---------- 策略1：趋势回调企稳 ----------


def strategy_trend_pullback_stabilize(code, df):
    """趋势回调企稳：平衡型候选池。

    目标不是只保留最教科书的企稳点，而是输出：
      - 标准企稳：趋势完整、回撤适中、缩量较好
      - 高风险修复：趋势未坏，但回撤偏深/缩量较弱/修复属性更强
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

    # 0. 回调前必须先有一段清晰上升，而不是底部反弹后横住
    if not _has_clear_uptrend(df):
        return None

    # 1. close 不明显跌破 MA20
    if close < ma20 * TREND_PULLBACK_RULES["close_vs_ma20_min"]:
        return None

    # 2. MA10 与 MA20 关系宽松些，但不能明显走坏；MA20 斜率向上（5天）
    if ma10 < ma20 * TREND_PULLBACK_RULES["ma10_vs_ma20_min"]:
        return None
    if len(df) < 6 or _nan(df.iloc[-6].get("ma20")):
        return None
    if ma20 <= float(df.iloc[-6]["ma20"]):
        return None

    # 3. 回撤至少要像一次正常回调，过深则直接放弃
    high_10d = float(df["high"].iloc[-10:].max())
    drawdown = (high_10d - close) / high_10d
    if (
        drawdown < TREND_PULLBACK_RULES["drawdown_min"]
        or drawdown > TREND_PULLBACK_RULES["drawdown_max"]
    ):
        return None

    # 4. 缩量转为评分项，但极端放量不做候选
    if len(df) < 8:
        return None
    vol_3 = df["volume"].iloc[-3:].mean()
    vol_5 = df["volume"].iloc[-8:-3].mean()
    if vol_5 <= 0:
        return None
    vol_ratio = float(vol_3 / vol_5)
    if vol_ratio > TREND_PULLBACK_RULES["vol_ratio_max"]:
        return None

    # 5. 价格位置：远离均线太多只适合追涨，不是回调企稳
    d10 = abs(close - ma10) / atr_val
    d20 = abs(close - ma20) / atr_val
    distance = min(d10, d20)
    if distance > TREND_PULLBACK_RULES["distance_atr_max"]:
        return None

    # 6. 不破位：允许稍深测试，但不能明显跌穿前低
    ref_low = float(
        df["low"].iloc[-10:-3].min() if len(df) >= 10 else df["low"].iloc[:-3].min()
    )
    recent_low = float(df["low"].iloc[-3:].min())
    support_line = ref_low * TREND_PULLBACK_RULES["support_line_ratio"]
    if recent_low < support_line and close < support_line:
        return None

    # 7. 近2日累计涨跌幅：急跌后尚未收敛时不保留
    chg_2d = float(last["change_pct"]) + float(prev["change_pct"])
    if chg_2d < TREND_PULLBACK_RULES["chg_2d_min"]:
        return None

    # 8. 至少出现一个企稳迹象
    signal = _detect_stabilization_signal(df)
    if signal is None:
        return None

    # --- 评分 ---
    score = 0.0
    risk_flags = []

    # 趋势强度 (30)
    if dif > 0:
        score += 10
    if d10 <= d20:
        score += 10
    if 0.03 <= drawdown <= 0.10:
        score += 10
    elif drawdown <= 0.14:
        score += 5
        risk_flags.append("回撤偏深")
    else:
        score += 2
        risk_flags.append("回撤偏深")
    if ma5 > ma10:
        score += 5

    # 缩量质量 (20)
    if vol_ratio <= 0.75:
        score += 15
    elif vol_ratio <= 0.95:
        score += 9
    elif vol_ratio <= 1.10:
        score += 4
        risk_flags.append("缩量一般")
    else:
        risk_flags.append("缩量不足")
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
            score += 5

    # 价格位置 (15)
    if distance <= 0.8:
        score += 15
    elif distance <= 1.2:
        score += 8
        risk_flags.append("偏离均线略远")
    else:
        score += 2
        risk_flags.append("偏离均线较远")

    # 近两日伤害 (10)
    if chg_2d > -3.5:
        score += 10
    else:
        score += 4
        risk_flags.append("急跌修复")

    if close < ma20:
        risk_flags.append("回踩MA20下方")

    # 形态 / 企稳迹象 (25)
    score += {
        "锤子线": 25,
        "双针探底": 22,
        "小阳企稳": 20,
        "十字星": 18,
        "缩量横住": 15,
        "长下影修复": 18,
        "跌速收敛": 15,
        "低点企稳": 14,
        "小实体止跌": 12,
    }.get(signal, 12)

    # MACD (10)
    if dif > dea:
        score += 8
    if len(df) >= 3:
        mh = [
            float(prev2["macd_hist"]),
            float(prev["macd_hist"]),
            float(last["macd_hist"]),
        ]
        if mh[2] > mh[1] > mh[0]:
            score += 2

    # 候选类型
    candidate_type = "标准企稳"
    high_risk_markers = {"回撤偏深", "缩量一般", "缩量不足", "偏离均线较远", "急跌修复", "回踩MA20下方"}
    if any(flag in high_risk_markers for flag in risk_flags):
        candidate_type = "高风险修复"

    risk_text = "无明显风险" if not risk_flags else "，".join(dict.fromkeys(risk_flags))

    return {
        "code": code,
        "name": "",
        "形态": signal,
        "候选类型": candidate_type,
        "风险说明": risk_text,
        "触发说明": signal,
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
