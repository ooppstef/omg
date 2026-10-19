"""筛选策略集合。

策略函数签名：func(code, df) -> dict | None
  code: 股票代码
  df: 已算好指标的 DataFrame
  返回 dict 表示命中，None 表示未命中
"""

import math
import talib

from stocks.universe import GROWTH_BOARD_CODE_PREFIXES


def _nan(val):
    if val is None:
        return True
    try:
        return not math.isfinite(float(val))
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
    "rally_min_days": 5,           # 上升段至少跨 5 个交易日
    "single_day_advance_max": 0.5,  # 单日涨幅不能贡献超过总涨幅的 50%
}

TREND_PULLBACK_RULES = {
    "adx_min": 20.0,
    "days_since_peak_min": 3,
    "days_since_peak_max": 10,
    "drawdown_atr_min": 1.2,
    "drawdown_atr_max": 4.8,
    "dryup_ratio_max": 0.85,
    "distribution_days_max": 1,
    "close_vs_ma20_min": 0.96,
    "close_vs_ma20_floor": 0.94,
    "rv20_repair_min": 0.70,
    "rv20_trigger_min": 1.0,
    "close_loc_trigger_min": 0.60,
}


# ---------- 止跌形态检测 ----------


def _detect_pattern(df):
    """检测最新K线止跌形态。优先级：锤子线 > 双针 > 小阳企稳 > 十字星 > 缩量横住。

    锤子线、十字星使用 TA-Lib 识别；双针探底、小阳企稳、缩量横住为自定义组合形态。
    """
    if len(df) < 3:
        return None

    op = df["open"].values.astype(float)
    hi = df["high"].values.astype(float)
    lo = df["low"].values.astype(float)
    cl = df["close"].values.astype(float)

    # TA-Lib: 锤子线 (Hammer)
    # 特征：小实体在K线上端，长下影线(≥实体2倍)，几乎无上影线，需前期处于下跌趋势
    # 含义：空方力竭，多方开始反击，看涨反转信号
    if talib.CDLHAMMER(op, hi, lo, cl)[-1] != 0:
        return "锤子线"

    # 双针探底（自定义：连续2日下影线/实体 >= 1.5）
    last, prev = df.iloc[-1], df.iloc[-2]
    body = abs(cl[-1] - op[-1])
    lower = min(cl[-1], op[-1]) - lo[-1]
    pb = abs(float(prev["close"]) - float(prev["open"]))
    pl_shadow = min(float(prev["close"]), float(prev["open"])) - float(prev["low"])
    if pb > 0 and body > 0 and lower >= body * 1.5 and pl_shadow >= pb * 1.5:
        return "双针探底"

    # 小阳企稳（自定义：涨跌幅 -0.5%~+1.5%，振幅收窄）
    chg = None if _nan(last.get("change_pct")) else float(last["change_pct"])
    amp = None if _nan(last.get("amplitude")) else float(last["amplitude"])
    if len(df) >= 4 and "amplitude" in df.columns:
        avg_amp = df["amplitude"].iloc[-4:-1].mean()
        if (
            chg is not None
            and amp is not None
            and -0.5 <= chg <= 1.5
            and amp > 0
            and avg_amp > 0
            and amp <= avg_amp * 0.8
        ):
            return "小阳企稳"

    # TA-Lib: 十字星 (Doji)
    # 特征：开盘价≈收盘价，实体极小，上下影线可长可短
    # 含义：多空力量均衡，趋势犹豫，常出现在变盘点；下跌末端出现暗示卖压衰竭
    if talib.CDLDOJI(op, hi, lo, cl)[-1] != 0:
        return "十字星"

    # 缩量横住（自定义：连续3日小实体，未创新低）
    prev2 = df.iloc[-3]
    p2b = abs(float(prev2["close"]) - float(prev2["open"]))
    thresh = 0.015
    all_small = all(
        b / max(ref, 1.0) < thresh
        for b, ref in [(body, cl[-1]), (pb, float(prev["close"])), (p2b, float(prev2["close"]))]
    )
    if all_small and lo[-1] >= min(float(prev["low"]), float(prev2["low"])) * 0.995:
        return "缩量横住"

    return None


def _pick_signal(df):
    """检测更宽松的企稳信号，优先返回严格形态。"""
    strict = _detect_pattern(df)
    if strict:
        return strict

    if len(df) < 3:
        return None

    last, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    o, h, low, c = (
        float(last["open"]),
        float(last["high"]),
        float(last["low"]),
        float(last["close"]),
    )
    body = abs(c - o)
    rng = h - low
    prev_low = float(prev["low"])
    prev2_low = float(prev2["low"])
    lower = min(c, o) - low
    changes = (
        last.get("change_pct"),
        prev.get("change_pct"),
        prev2.get("change_pct"),
    )
    if any(_nan(value) for value in changes):
        return None
    chg, prev_chg, prev2_chg = (float(value) for value in changes)

    # 长下影修复：下影明显长于实体，且尾盘没有失控走弱。
    if rng > 0 and body > 0 and lower >= max(body * 1.8, rng * 0.35) and chg >= -1.5:
        return "长下影修复"

    # 跌速收敛：前一日杀跌更重，当日跌幅明显收窄。
    if prev_chg < 0 and chg >= prev_chg * 0.7 and chg > prev_chg and prev_chg <= prev2_chg:
        return "跌速收敛"

    # 低点企稳：最近不再创新低，且实体收窄。
    if low >= min(prev_low, prev2_low) * 0.995 and body / max(c, 1.0) <= 0.015:
        return "低点企稳"

    # 小实体止跌：实体小，跌幅温和。
    if rng > 0 and body / max(c, 1.0) <= 0.012 and chg >= -1.2:
        return "小实体止跌"

    return None


def _has_clear_uptrend(df):
    """判断回调前是否存在清晰上升段。"""
    if len(df) < 25:
        return False

    data = df.reset_index(drop=True)
    window = data.iloc[-CLEAR_UPTREND_RULES["window"] :]
    peak_idx = window["high"].idxmax()
    peak = data.loc[peak_idx]

    if _nan(peak.get("ma10")) or _nan(peak.get("ma20")):
        return False

    peak_high = float(peak["high"])
    peak_ma20 = float(peak["ma20"])
    if peak_ma20 <= 0:
        return False

    start_idx = max(0, peak_idx - CLEAR_UPTREND_RULES["lookback"])
    seg = data.iloc[start_idx : peak_idx + 1]
    seg = data.iloc[seg["low"].idxmin() : peak_idx + 1]
    rally_low = float(seg["low"].min())
    if rally_low <= 0:
        return False

    advance = (peak_high - rally_low) / rally_low
    peak_spread = peak_high / peak_ma20 - 1.0

    # 上升段必须是渐进的：跨度 >= N 天，且单日最大涨幅不能贡献超过总涨幅的 50%
    if len(seg) < CLEAR_UPTREND_RULES["rally_min_days"]:
        return False
    if advance > 0:
        max_single_day = float(seg["change_pct"].max()) / 100.0
        if max_single_day / advance > CLEAR_UPTREND_RULES["single_day_advance_max"]:
            return False

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


def _trend_state(df):
    """评估趋势背景。"""
    state = {"ok": False, "score": 0.0, "notes": [], "risks": []}
    need = ("ma20", "ma60", "adx", "plus_di", "minus_di", "ppo", "dif")
    if len(df) < 6:
        state["notes"].append("趋势样本不足")
        return state
    for col in need:
        if col not in df.columns or _nan(df.iloc[-1].get(col)):
            state["notes"].append(f"缺少趋势列:{col}")
            return state

    last = df.iloc[-1]
    ma20 = float(last["ma20"])
    ma60 = float(last["ma60"])
    adx = float(last["adx"])
    plus_di = float(last["plus_di"])
    minus_di = float(last["minus_di"])
    ppo = float(last["ppo"])
    dif = float(last["dif"])

    ma20_old = None
    if len(df) >= 6 and not _nan(df.iloc[-6].get("ma20")):
        ma20_old = float(df.iloc[-6]["ma20"])

    ma_alignment_ok = ma20 > ma60
    ma_slope_ok = ma20_old is not None and ma20 > ma20_old
    adx_ok = adx >= TREND_PULLBACK_RULES["adx_min"]
    di_ok = plus_di > minus_di
    momentum_ok = ppo > 0 or dif > 0

    if ma_alignment_ok:
        state["score"] += 10
    else:
        state["notes"].append("MA20 未站上 MA60")

    if ma_slope_ok:
        state["score"] += 8
    else:
        state["notes"].append("MA20 斜率不足")

    if adx_ok:
        state["score"] += 8
    elif adx >= TREND_PULLBACK_RULES["adx_min"] - 2:
        state["score"] += 4
        state["risks"].append("ADX偏弱")
    else:
        state["notes"].append("ADX不足")

    if di_ok:
        state["score"] += 6
    else:
        state["notes"].append("+DI 未压制 -DI")

    if momentum_ok:
        state["score"] += 8
    else:
        state["notes"].append("PPO / DIF 仍偏弱")

    rally_ready = True
    if {"high", "low", "close", "change_pct"}.issubset(df.columns) and len(df) >= 25:
        rally_ready = _has_clear_uptrend(df)
        if rally_ready:
            state["score"] += 10
        else:
            state["notes"].append("回调前上升段不够清晰")

    state["ok"] = (
        ma_alignment_ok and ma_slope_ok and adx_ok and di_ok and momentum_ok and rally_ready
    )
    return state


def _is_distribution_day(row):
    """识别放量阴线派发。"""
    if _nan(row.get("rv20")) or _nan(row.get("close_loc")) or _nan(row.get("change_pct")):
        return False
    return (
        float(row["rv20"]) > 1.8
        and float(row["close_loc"]) < 0.25
        and float(row["change_pct"]) < 0
    )


def _pullback_state(df):
    """评估回调质量。"""
    state = {
        "ok": False,
        "score": 0.0,
        "notes": [],
        "risks": [],
        "dryup_ratio": float("nan"),
        "distribution_cluster": False,
        "drawdown": float("nan"),
        "drawdown_atr": float("nan"),
        "days_since_peak": 0,
    }
    need = ("high", "low", "close", "volume", "ma20", "atr", "change_pct", "rv20", "close_loc")
    for col in need:
        if col not in df.columns or _nan(df.iloc[-1].get(col)):
            state["notes"].append(f"缺少回调列:{col}")
            return state

    data = df.reset_index(drop=True)
    window = data.iloc[-20:] if len(data) >= 20 else data
    peak_idx = window["high"].idxmax()
    last = data.iloc[-1]
    close = float(last["close"])
    peak_high = float(data.loc[peak_idx, "high"])
    atr_val = float(last["atr"])
    ma20 = float(last["ma20"])
    if atr_val <= 0 or peak_high <= 0 or ma20 <= 0:
        state["notes"].append("回调基础数据异常")
        return state

    days_since_peak = len(data) - 1 - peak_idx
    state["days_since_peak"] = days_since_peak
    drawdown = (peak_high - close) / peak_high
    drawdown_atr = (peak_high - close) / atr_val
    state["drawdown"] = drawdown
    state["drawdown_atr"] = drawdown_atr

    up_leg = data.iloc[max(0, peak_idx - 10) : peak_idx + 1]
    pullback = data.iloc[peak_idx + 1 :]
    if pullback.empty:
        state["notes"].append("尚未形成回调段")
        return state

    up_days = up_leg[up_leg["change_pct"] > 0]
    down_days = pullback[pullback["change_pct"] < 0]
    if not up_days.empty and not down_days.empty and float(up_days["volume"].mean()) > 0:
        dryup_ratio = float(down_days["volume"].mean() / up_days["volume"].mean())
    else:
        dryup_ratio = 1.0
    state["dryup_ratio"] = dryup_ratio

    distribution_days = sum(_is_distribution_day(row) for _, row in pullback.iterrows())
    state["distribution_cluster"] = distribution_days > TREND_PULLBACK_RULES["distribution_days_max"]

    days_ok = (
        TREND_PULLBACK_RULES["days_since_peak_min"]
        <= days_since_peak
        <= TREND_PULLBACK_RULES["days_since_peak_max"]
    )
    drawdown_ok = (
        TREND_PULLBACK_RULES["drawdown_atr_min"]
        <= drawdown_atr
        <= TREND_PULLBACK_RULES["drawdown_atr_max"]
    )
    close_vs_ma20 = close / ma20
    close_ok = close_vs_ma20 >= TREND_PULLBACK_RULES["close_vs_ma20_floor"]

    if days_ok:
        state["score"] += 8
    else:
        state["notes"].append("回调时长不在目标区间")

    if drawdown_ok:
        state["score"] += 12
    elif drawdown_atr <= TREND_PULLBACK_RULES["drawdown_atr_max"] + 0.8:
        state["score"] += 6
        state["risks"].append("回调偏深")
    else:
        state["notes"].append("回撤超出可接受范围")

    if dryup_ratio <= TREND_PULLBACK_RULES["dryup_ratio_max"]:
        state["score"] += 10
    elif dryup_ratio <= 1.0:
        state["score"] += 5
        state["risks"].append("缩量一般")
    else:
        state["notes"].append("缩量不足")

    if close_vs_ma20 >= TREND_PULLBACK_RULES["close_vs_ma20_min"]:
        state["score"] += 8
    elif close_ok:
        state["score"] += 4
        state["risks"].append("回踩MA20偏深")
    else:
        state["notes"].append("已明显跌破 MA20")

    if not state["distribution_cluster"]:
        state["score"] += 8
    else:
        state["notes"].append("回调中出现派发簇")

    if "obv" in data.columns and not _nan(last.get("obv")):
        obv_ref = pullback["obv"].iloc[:-1] if len(pullback) > 1 else pullback["obv"]
        if not obv_ref.empty and float(last["obv"]) >= float(obv_ref.min()):
            state["score"] += 4
        else:
            state["risks"].append("OBV修复偏弱")

    state["ok"] = days_ok and drawdown_ok and close_ok and dryup_ratio <= 1.0 and not state["distribution_cluster"]
    return state


def _trigger_state(df, signal):
    """评估触发质量。"""
    state = {"ok": False, "score": 0.0, "notes": [], "risks": []}
    if signal is None:
        state["notes"].append("缺少企稳信号")
        return state

    need = ("close", "ma5", "high", "rv20", "close_loc", "ppo_hist", "ppo", "ppo_signal", "adosc")
    if len(df) < 3:
        state["notes"].append("触发样本不足")
        return state
    for col in need:
        if col not in df.columns or _nan(df.iloc[-1].get(col)):
            state["notes"].append(f"缺少触发列:{col}")
            return state

    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    last = df.iloc[-1]
    close = float(last["close"])
    ma5 = float(last["ma5"])
    rv20 = float(last["rv20"])
    close_loc = float(last["close_loc"])

    reclaim_ok = close >= ma5 and close >= float(prev["high"]) * 0.995
    early_reclaim_ok = close >= ma5 * 0.99
    volume_ok = rv20 >= TREND_PULLBACK_RULES["rv20_trigger_min"]
    close_loc_ok = close_loc >= TREND_PULLBACK_RULES["close_loc_trigger_min"]
    close_loc_repair_ok = close_loc >= 0.70

    ppo_hist = [float(prev2["ppo_hist"]), float(prev["ppo_hist"]), float(last["ppo_hist"])]
    gap_prev = float(prev["ppo_signal"]) - float(prev["ppo"])
    gap_last = float(last["ppo_signal"]) - float(last["ppo"])
    ppo_repair_ok = ppo_hist[2] > ppo_hist[1] > ppo_hist[0] and gap_last < gap_prev
    ppo_repair_soft = ppo_hist[2] > ppo_hist[1] or gap_last < gap_prev

    adosc_ok = float(last["adosc"]) > float(prev["adosc"])
    obv_rising = False
    if "obv" in df.columns and not _nan(last.get("obv")) and not _nan(prev.get("obv")):
        obv_rising = float(last["obv"]) > float(prev["obv"])

    state["score"] += {
        "锤子线": 12,
        "双针探底": 11,
        "小阳企稳": 10,
        "十字星": 9,
        "缩量横住": 8,
        "长下影修复": 9,
        "跌速收敛": 8,
        "低点企稳": 8,
        "小实体止跌": 7,
    }.get(signal, 7)

    if reclaim_ok:
        state["score"] += 10
    elif early_reclaim_ok:
        state["score"] += 5
        state["risks"].append("尚未完成突破")
    else:
        state["notes"].append("价格未有效收复")

    if rv20 >= TREND_PULLBACK_RULES["rv20_trigger_min"] + 0.2:
        state["score"] += 10
    elif volume_ok:
        state["score"] += 8
    elif rv20 >= TREND_PULLBACK_RULES["rv20_repair_min"]:
        state["score"] += 4
        state["risks"].append("触发量能不足")
    else:
        state["notes"].append("触发量能不足")

    if close_loc >= 0.75:
        state["score"] += 10
    elif close_loc_ok:
        state["score"] += 8
    else:
        state["notes"].append("收盘位置偏弱")

    if ppo_repair_ok:
        state["score"] += 12
    elif ppo_repair_soft:
        state["score"] += 5
        state["risks"].append("PPO修复一般")
    else:
        state["notes"].append("PPO修复不足")

    if adosc_ok:
        state["score"] += 8
    elif obv_rising:
        state["score"] += 4
        state["risks"].append("资金流确认一般")
    else:
        state["notes"].append("资金流确认不足")

    full_trigger_ok = reclaim_ok and volume_ok and close_loc_ok and ppo_repair_ok and adosc_ok
    repair_ok = (
        early_reclaim_ok
        and close_loc_repair_ok
        and (ppo_repair_soft or adosc_ok or obv_rising)
    )
    state["ok"] = signal is not None and (full_trigger_ok or repair_ok)
    return state


# ---------- 策略1：趋势回调企稳 ----------


def trend_pullback(code, df):
    """趋势回调企稳：Trend / Pullback / Trigger 状态机。"""
    if len(df) < 60:
        return None

    last = df.iloc[-1]

    # 必要列检查
    need = (
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "close",
        "volume",
        "atr",
        "dif",
        "dea",
        "macd_hist",
        "adx",
        "plus_di",
        "minus_di",
        "ppo",
        "ppo_signal",
        "ppo_hist",
        "rv20",
        "close_loc",
        "adosc",
    )
    for col in need:
        if col not in df.columns or _nan(last.get(col)):
            return None

    close = float(last["close"])
    ma10, ma20 = float(last["ma10"]), float(last["ma20"])
    atr_val = float(last["atr"])
    dif, dea = float(last["dif"]), float(last["dea"])
    macd_h = float(last["macd_hist"])

    if atr_val <= 0 or float(last["volume"]) <= 0:
        return None

    # 最低股价过滤
    if close < 5.0:
        return None

    # 近 15 日内不能有 ≥2 个近涨停日，创业板/科创板按 20% 口径。
    recent_15 = df["change_pct"].iloc[-15:]
    limit_threshold = 19.5 if str(code).startswith(GROWTH_BOARD_CODE_PREFIXES) else 9.5
    if (recent_15 >= limit_threshold).sum() >= 2:
        return None

    signal = _pick_signal(df)
    trend = _trend_state(df)
    if not trend["ok"]:
        return None
    pullback = _pullback_state(df)
    if not pullback["ok"]:
        return None
    trigger = _trigger_state(df, signal)
    if not trigger["ok"]:
        return None

    trend_score = trend["score"]
    pullback_score = pullback["score"]
    trigger_score = trigger["score"]
    score = trend_score + pullback_score + trigger_score

    risks = list(dict.fromkeys(trend["risks"] + pullback["risks"] + trigger["risks"]))
    cand_type = "标准企稳" if not risks else "高风险修复"
    risk_text = "无明显风险" if not risks else "，".join(risks)

    return {
        "code": code,
        "name": "",
        "形态": signal,
        "候选类型": cand_type,
        "风险说明": risk_text,
        "触发说明": f"{signal} / Trend:{int(trend_score)} Pullback:{int(pullback_score)} Trigger:{int(trigger_score)}",
        "总分": round(score, 1),
        "趋势分": round(trend_score, 1),
        "回调分": round(pullback_score, 1),
        "触发分": round(trigger_score, 1),
        "close": round(close, 2),
        "MA10": round(ma10, 2),
        "MA20": round(ma20, 2),
        "回撤": f"{pullback['drawdown'] * 100:.1f}%",
        "缩量比": f"{pullback['dryup_ratio']:.2f}",
        "DIF": round(dif, 3),
        "DEA": round(dea, 3),
        "MACD": round(macd_h, 3),
        "ATR": round(atr_val, 2),
    }


# 策略注册表：菜单序号 -> (名称, 函数)
STRATEGIES = {
    "1": ("趋势回调企稳", trend_pullback),
}
