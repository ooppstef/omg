import adata
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

from stocks.util import env_int, recent_date

# 常量配置
WARM_THRESH = 50
PULL_THRESH = 55
STRONG_MIN = 1.5
LOOKBACK = 8
RECENT_DAYS = 2
WARM_DAYS = 3

CONCEPT_WORKERS = env_int("OMG_ROT_CONCEPT_WORKERS", 20)
STOCK_WORKERS = env_int("OMG_ROT_STOCK_WORKERS", 20)

get_recent_date = recent_date


def _amount_mult(amount_series):
    """成交额放大因子，用 log 压缩避免大额主导。"""
    amounts = pd.to_numeric(amount_series, errors="coerce").fillna(0.0)
    median = amounts.median()
    if pd.isna(median) or median <= 0:
        median = 1.0
    return amounts.apply(lambda a: 1.0 + math.log1p(max(a, 0.0) / median))


def daily_strength(concept_df, benchmark):
    """构建概念板块的日强度序列。"""
    cols = ["trade_date", "change_pct", "amount", "excess_return", "daily_strength"]
    if concept_df is None or concept_df.empty:
        return pd.DataFrame(columns=cols)

    df = concept_df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0.0)
    df["amount"] = pd.to_numeric(df.get("amount", 0.0), errors="coerce").fillna(0.0)

    df["benchmark_change_pct"] = df["trade_date"].map(benchmark)
    df = df.dropna(subset=["benchmark_change_pct"]).copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["excess_return"] = df["change_pct"] - df["benchmark_change_pct"]
    df["daily_strength"] = df["excess_return"] * _amount_mult(df["amount"])
    return df[cols]


def _clamp(value, lower=0.0, upper=100.0):
    return max(lower, min(upper, float(value)))


def _stock_score(feat):
    avg = float(feat.get("top5_avg_change_pct", 0.0) or 0.0)
    pos = float(feat.get("positive_ratio", 0.0) or 0.0)
    conc = float(feat.get("leader_concentration", 0.0) or 0.0)
    return _clamp(
        _clamp((avg + 2.0) / 5.0 * 40.0, upper=40.0)
        + _clamp(pos * 30.0, upper=30.0)
        + _clamp(conc * 30.0, upper=30.0)
    )


def _heat_score(hot_row):
    if not hot_row:
        return 0.0
    rank = hot_row.get("rank")
    hot_val = hot_row.get("hot_value")
    rs = 0.0
    hs = 0.0
    if rank is not None and not pd.isna(rank):
        rank = max(float(rank), 1.0)
        rs = _clamp((21.0 - rank) / 20.0 * 50.0, upper=50.0)
    if hot_val is not None and not pd.isna(hot_val):
        hs = _clamp(float(hot_val) / 100.0 * 50.0, upper=50.0)
    return _clamp(rs + hs)


def _format_hot_tag(value):
    if value is None or pd.isna(value):
        return "-"
    return str(value)


def _fmt_signed(value, digits=1):
    return f"{float(value):+.{digits}f}"


def _fmt_ratio(value, digits=2):
    return f"{float(value):.{digits}f}"


def _normalize_code(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _concept_row_to_fetch_target(row):
    name = row.get("name")
    index_code = _normalize_code(row.get("index_code"))
    concept_code = _normalize_code(row.get("concept_code"))
    if not name or not index_code:
        return None
    return {"name": name, "index_code": index_code, "concept_code": concept_code}


def _match_hot_row(target, hot_by_code, hot_by_name):
    concept_code = target.get("concept_code")
    index_code = target.get("index_code")
    name = target.get("name")
    return (
        hot_by_code.get(concept_code)
        or hot_by_code.get(index_code)
        or hot_by_name.get(name)
    )


def _pullback_volume_score(strength_df):
    """回流量能加分：偏好缩量企稳，惩罚弱势放量。"""
    if strength_df is None or strength_df.empty:
        return {
            "score": 0.0,
            "recent_amount_ratio": 1.0,
            "recent_mean_strength": 0.0,
            "latest_strength": 0.0,
            "prev_strength": 0.0,
            "reengage": False,
            "weak_expansion": False,
        }

    tail = strength_df.sort_values("trade_date").tail(LOOKBACK).copy()
    recent = tail.tail(RECENT_DAYS)
    prior = tail.iloc[:-RECENT_DAYS] if len(tail) > RECENT_DAYS else tail

    amounts = pd.to_numeric(tail["amount"], errors="coerce").fillna(0.0)
    recent_amounts = pd.to_numeric(recent["amount"], errors="coerce").fillna(0.0)
    prior_amounts = pd.to_numeric(prior["amount"], errors="coerce").fillna(0.0)
    strengths = pd.to_numeric(tail["daily_strength"], errors="coerce").fillna(0.0)
    recent_strengths = pd.to_numeric(recent["daily_strength"], errors="coerce").fillna(0.0)

    prior_median = float(prior_amounts.median()) if not prior_amounts.empty else float(amounts.median())
    if prior_median <= 0:
        prior_median = 1.0

    recent_mean_amount = float(recent_amounts.mean()) if not recent_amounts.empty else 0.0
    recent_ratio = recent_mean_amount / prior_median
    recent_mean_strength = float(recent_strengths.mean()) if not recent_strengths.empty else 0.0
    latest_strength = float(strengths.iloc[-1]) if not strengths.empty else 0.0
    prev_strength = float(strengths.iloc[-2]) if len(strengths) >= 2 else latest_strength
    latest_amount = float(recent_amounts.iloc[-1]) if not recent_amounts.empty else 0.0

    contraction_bonus = _clamp((1.15 - recent_ratio) / 0.55 * 35.0, 0.0, 35.0)
    weak_expansion_penalty = 0.0
    if recent_ratio > 1.1 and recent_mean_strength < -0.2:
        weak_expansion_penalty = _clamp((recent_ratio - 1.1) * 45.0 + abs(recent_mean_strength) * 20.0, 0.0, 50.0)

    reengage_bonus = 0.0
    recent_baseline = max(recent_mean_amount, 1.0)
    reengage = latest_strength > prev_strength and latest_strength > -0.2 and latest_amount >= recent_baseline
    if reengage:
        reengage_bonus = _clamp((latest_amount / recent_baseline - 1.0) * 40.0 + (latest_strength - prev_strength) * 15.0, 0.0, 20.0)

    return {
        "score": round(_clamp(50.0 + contraction_bonus + reengage_bonus - weak_expansion_penalty), 2),
        "recent_amount_ratio": round(recent_ratio, 2),
        "recent_mean_strength": round(recent_mean_strength, 2),
        "latest_strength": round(latest_strength, 2),
        "prev_strength": round(prev_strength, 2),
        "reengage": reengage,
        "weak_expansion": weak_expansion_penalty > 0,
    }


def _warming_volume_score(strength_df):
    """升温量能加分：偏好强度抬升时量能同步扩张。"""
    if strength_df is None or strength_df.empty:
        return {
            "score": 0.0,
            "latest_vs_median": 1.0,
            "latest_vs_first": 1.0,
            "strength_slope": 0.0,
            "expanding": False,
            "contracting": False,
        }

    tail = strength_df.sort_values("trade_date").tail(WARM_DAYS).copy()
    amounts = pd.to_numeric(tail["amount"], errors="coerce").fillna(0.0)
    strengths = pd.to_numeric(tail["daily_strength"], errors="coerce").fillna(0.0)

    if amounts.empty:
        return {
            "score": 0.0,
            "latest_vs_median": 1.0,
            "latest_vs_first": 1.0,
            "strength_slope": 0.0,
            "expanding": False,
            "contracting": False,
        }

    latest_amount = float(amounts.iloc[-1])
    first_amount = float(amounts.iloc[0]) if len(amounts) >= 1 else latest_amount
    median_amount = float(amounts.median()) if not amounts.empty else latest_amount
    amount_slope = float(amounts.iloc[-1] - amounts.iloc[0]) if len(amounts) >= 2 else 0.0
    strength_slope = float(strengths.iloc[-1] - strengths.iloc[0]) if len(strengths) >= 2 else 0.0

    latest_vs_median = latest_amount / median_amount if median_amount > 0 else 1.0
    latest_vs_first = latest_amount / first_amount if first_amount > 0 else 1.0
    slope_ratio = amount_slope / median_amount if median_amount > 0 else 0.0

    expansion_bonus = _clamp((max(latest_vs_median, latest_vs_first) - 1.0) * 45.0, 0.0, 40.0)
    trend_bonus = _clamp(max(slope_ratio, 0.0) * 25.0, 0.0, 20.0)
    contraction_penalty = 0.0
    contracting = strength_slope > 0 and latest_vs_median < 0.95 and latest_vs_first < 0.95
    if contracting:
        contraction_penalty = _clamp((0.95 - min(latest_vs_median, latest_vs_first)) * 70.0, 0.0, 35.0)

    return {
        "score": round(_clamp(45.0 + expansion_bonus + trend_bonus - contraction_penalty), 2),
        "latest_vs_median": round(latest_vs_median, 2),
        "latest_vs_first": round(latest_vs_first, 2),
        "strength_slope": round(strength_slope, 2),
        "expanding": max(latest_vs_median, latest_vs_first) > 1.0,
        "contracting": contracting,
    }


def stock_confirm(stock_df):
    """用成分股 top5 强弱确认板块承接力。"""
    empty = {"top5_avg_change_pct": 0.0, "positive_ratio": 0.0, "leader_concentration": 0.0}
    if stock_df is None or stock_df.empty:
        return empty

    df = stock_df.copy()
    df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0.0)
    df = df.sort_values("change_pct", ascending=False)
    top5 = df.head(5)
    positive = top5[top5["change_pct"] > 0]["change_pct"]

    pos_sum = float(positive.sum())
    return {
        "top5_avg_change_pct": round(float(top5["change_pct"].mean()), 2),
        "positive_ratio": round(float((top5["change_pct"] > 0).mean()), 2),
        "leader_concentration": round(float(positive.head(2).sum() / pos_sum), 2) if pos_sum > 0 else 0.0,
    }


def pullback_score(strength_df, hot_row, feat):
    """回流优先分数：前强后调 + 缩量企稳确认。"""
    if strength_df is None or strength_df.empty:
        return {"score": 0.0, "gate_passed": False, "was_strong": False}

    tail = strength_df.sort_values("trade_date").tail(LOOKBACK).copy()
    s = pd.to_numeric(tail["daily_strength"], errors="coerce").fillna(0.0)
    prior = s.iloc[:-RECENT_DAYS] if len(s) > RECENT_DAYS else s
    recent = s.tail(RECENT_DAYS)

    thresh = max(STRONG_MIN, float(prior.quantile(0.75)) if not prior.empty else 0.0)
    strong_days = int((prior >= thresh).sum())
    was_strong = strong_days >= 1
    max_s = float(prior.max()) if not prior.empty else 0.0

    recent_mean = float(recent.mean()) if not recent.empty else 0.0
    recent_min = float(recent.min()) if not recent.empty else 0.0
    volume_res = _pullback_volume_score(tail)
    volume_s = volume_res["score"]

    score = (
        _clamp(strong_days / 2.0 * 70.0 + max_s / 5.0 * 30.0) * 0.35
        + _clamp(100.0 - abs(recent_mean + 0.4) * 80.0 - max(abs(recent_min) - 2.0, 0.0) * 20.0) * 0.25
        + _stock_score(feat) * 0.2
        + _heat_score(hot_row) * 0.1
        + volume_s * 0.1
    )

    gate = was_strong and -1.5 < recent_mean <= 0.3 and recent_min > -3.0

    return {
        "score": round(_clamp(score), 2),
        "gate_passed": gate,
        "was_strong": was_strong,
        "strong_days": strong_days,
        "recent_mean": round(recent_mean, 2),
        "volume_score": volume_s,
        "recent_amount_ratio": volume_res["recent_amount_ratio"],
        "reengage": volume_res["reengage"],
        "weak_expansion": volume_res["weak_expansion"],
    }


def warming_score(strength_df, hot_row, feat):
    """升温接力分数：强度抬升 + 量能共振。"""
    if strength_df is None or strength_df.empty:
        return {"score": 0.0}

    tail = strength_df.sort_values("trade_date").tail(WARM_DAYS).copy()
    s = pd.to_numeric(tail["daily_strength"], errors="coerce").fillna(0.0)
    amt = pd.to_numeric(tail["amount"], errors="coerce").fillna(0.0)

    slope = float(s.iloc[-1] - s.iloc[0]) if len(s) >= 2 else 0.0
    diffs = s.diff().dropna()
    vol_pen = float(diffs.std()) if len(diffs) > 1 else 0.0
    latest_amt = float(amt.iloc[-1]) if not amt.empty else 0.0
    first_amt = float(amt.iloc[0]) if not amt.empty else 0.0
    med_amt = float(amt.median()) if not amt.empty else 0.0

    slope_s = _clamp((slope + 1.0) / 3.0 * 100.0 - vol_pen * 20.0)
    med_r = latest_amt / med_amt if med_amt else 1.0
    first_r = latest_amt / first_amt if first_amt else 1.0
    amt_s = _clamp((max(med_r, first_r) - 1.0) * 100.0)
    volume_res = _warming_volume_score(tail)
    volume_s = volume_res["score"]

    score = slope_s * 0.35 + amt_s * 0.15 + _heat_score(hot_row) * 0.15 + _stock_score(feat) * 0.20 + volume_s * 0.15
    return {
        "score": round(_clamp(score), 2),
        "slope": round(slope, 2),
        "volume_score": volume_s,
        "latest_vs_median": volume_res["latest_vs_median"],
        "latest_vs_first": volume_res["latest_vs_first"],
        "expanding": volume_res["expanding"],
        "contracting": volume_res["contracting"],
    }


def classify(*, pull, warm, gate, was_strong):
    """根据回流/升温分数输出分组。"""
    if gate and was_strong and pull >= PULL_THRESH:
        return "回流优先"
    if warm >= WARM_THRESH:
        return "升温接力"
    return "放弃观察"


def _get_benchmark(start_date, lookback_days=None):
    """获取沪深300基准涨跌幅。"""
    try:
        df = adata.stock.market.get_market_index(index_code="000300", start_date=start_date)
    except Exception as e:
        print(f"获取沪深300基准失败: {e}")
        return {}
    if df is None or df.empty:
        return {}
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0.0)
    df = df.sort_values("trade_date")
    if lookback_days:
        df = df.tail(lookback_days)
    return dict(zip(df["trade_date"], df["change_pct"]))


def _fetch_concepts(all_concepts, start_date):
    """并发加载概念历史数据。"""
    if all_concepts is None or all_concepts.empty:
        return []

    targets = []
    for _, row in all_concepts.iterrows():
        target = _concept_row_to_fetch_target(row)
        if target:
            targets.append(target)

    def fetch_one(target):
        name = target["name"]
        index_code = target["index_code"]
        if not name or not index_code:
            return None
        try:
            df = adata.stock.market.get_market_concept_ths(index_code=index_code)
        except Exception as e:
            if "8开头的指数代码" in str(e):
                return None
            return {"name": name, "index_code": index_code, "concept_code": target.get("concept_code"), "hist": None, "error": e}
        if df is None or df.empty:
            return None
        df = df.copy()
        df["trade_date"] = df["trade_date"].astype(str).str[:10]
        df = df[df["trade_date"] >= start_date].sort_values("trade_date")
        if len(df) < 3:
            return None
        return {
            "name": name,
            "index_code": index_code,
            "concept_code": target.get("concept_code"),
            "hist": df,
            "error": None,
        }

    loaded = []
    with ThreadPoolExecutor(max_workers=CONCEPT_WORKERS) as executor:
        futures = [executor.submit(fetch_one, target) for target in targets]
        for f in as_completed(futures):
            r = f.result()
            if r:
                loaded.append(r)
    return loaded


def _get_stock_perf(stock_codes):
    """批量获取个股实时表现。"""
    if not stock_codes:
        return []
    try:
        df = adata.stock.market.list_market_current(stock_codes)
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            chg = row["change_pct"]
            price = row["price"]
            results.append({
                "code": row["stock_code"],
                "name": row["short_name"],
                "change_pct": 0.0 if pd.isna(chg) else float(chg),
                "price": 0.0 if pd.isna(price) else float(price),
            })
        return results
    except Exception as e:
        print(f"获取实时行情失败: {e}")
        return []


def _fetch_stocks(code, top_n):
    """获取单个概念的成分股确认信息。"""
    try:
        df = adata.stock.info.concept_constituent_ths(index_code=code)
    except Exception as e:
        print(f"获取成分股失败({code}): {e}")
        return stock_confirm(pd.DataFrame()), []
    if df is None or df.empty:
        return stock_confirm(pd.DataFrame()), []

    # 只保留沪深主板/创业板/科创板，排除ST
    valid_prefixes = ("600", "00", "300", "688")
    codes = []
    for c in df["stock_code"].tolist():
        if str(c).startswith(valid_prefixes):
            codes.append(c)

    all_perfs = _get_stock_perf(codes)
    perfs = []
    for p in all_perfs:
        name = str(p.get("name", "")).upper()
        if "ST" not in name:
            perfs.append(p)
    feat = stock_confirm(pd.DataFrame(perfs))
    perfs.sort(key=lambda x: x.get("change_pct", 0.0), reverse=True)
    return feat, perfs[:top_n]


def _fetch_stocks_batch(candidates, top_n):
    """并发加载候选板块的成分股确认。"""
    if not candidates:
        return {}

    def fetch_one(c):
        feat, tops = _fetch_stocks(c["code"], top_n)
        return c["code"], {"feat": feat, "tops": tops}

    loaded = {}
    workers = min(STOCK_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_one, c) for c in candidates]
        for f in as_completed(futures):
            code, result = f.result()
            loaded[code] = result
    return loaded


def _pick_cands(pending, top_concepts):
    """选择需要做成分股确认的候选，兼顾硬条件和边缘候选补位。"""
    selected = {}

    for candidate in pending:
        needs = (
            bool(candidate["hot_row"])
            or candidate["pr"]["gate_passed"]
            or candidate["wr"]["score"] >= WARM_THRESH - 10
        )
        if needs:
            selected[candidate["code"]] = candidate

    remainder = [
        candidate
        for candidate in pending
        if candidate["code"] not in selected
        and (candidate["wr"]["score"] > 0 or candidate["pr"]["was_strong"])
    ]
    remainder.sort(
        key=lambda item: max(item["wr"]["score"], item["pr"]["score"]),
        reverse=True,
    )

    for candidate in remainder[:top_concepts]:
        selected[candidate["code"]] = candidate

    return list(selected.values())


def _reason(group, pull_res, warm_res, feat, hot_row):
    """生成分组原因文字。"""
    lead = ""
    parts = []
    hot_rank = hot_row.get("rank") if hot_row else None
    pos = float(feat.get("positive_ratio", 0.0) or 0.0)
    top5_avg = float(feat.get("top5_avg_change_pct", 0.0) or 0.0)

    if group == "回流优先":
        if pull_res.get("reengage"):
            lead = "前强回踩，承接恢复"
        elif float(pull_res.get("recent_amount_ratio", 1.0) or 1.0) < 0.95:
            lead = "前强后调，缩量企稳"
        else:
            lead = "前强后调，承接未坏"

        parts.append(f"前强{int(pull_res.get('strong_days', 0) or 0)}天")
        parts.append(f"近2日回调均值{_fmt_signed(pull_res.get('recent_mean', 0.0))}")
        parts.append(f"量能比{_fmt_ratio(pull_res.get('recent_amount_ratio', 1.0))}")
        if pull_res.get("reengage"):
            parts.append("末日承接回升")
        elif hot_rank:
            parts.append(f"热榜第{int(hot_rank)}")
        if pos >= 0.8:
            parts.append(f"Top5全强，均涨{_fmt_signed(top5_avg)}%")
        elif pos >= 0.4:
            parts.append(f"Top5有{int(round(pos * 5))}只翻红")
    elif group == "升温接力":
        if warm_res.get("expanding"):
            lead = "近3日升温，量能配合"
        else:
            lead = "近3日升温，扩散改善"

        parts.append(f"强度抬升{_fmt_signed(warm_res.get('slope', 0.0))}")
        parts.append(f"末日量能比{_fmt_ratio(max(warm_res.get('latest_vs_median', 1.0), warm_res.get('latest_vs_first', 1.0)))}")
        if pos >= 0.6:
            parts.append(f"Top5有{int(round(pos * 5))}只翻红")
        elif top5_avg:
            parts.append(f"Top5均涨{_fmt_signed(top5_avg)}%")
        if hot_rank:
            parts.append(f"热榜第{int(hot_rank)}")
    else:
        if pull_res.get("was_strong"):
            if pull_res.get("weak_expansion"):
                lead = "前强基础仍在，但放量修复不足"
            elif float(pull_res.get("recent_mean", 0.0) or 0.0) < -0.6:
                lead = "前强基础仍在，但回调偏深"
            else:
                lead = "前强基础仍在，但修复不足"
        else:
            lead = "结构转弱，暂不跟踪"

        parts.append(f"近2日均值{_fmt_signed(pull_res.get('recent_mean', 0.0))}")
        parts.append(f"量能比{_fmt_ratio(pull_res.get('recent_amount_ratio', 1.0))}")
        if hot_rank:
            parts.append(f"热榜第{int(hot_rank)}")
        if pos >= 0.6:
            parts.append(f"Top5有{int(round(pos * 5))}只翻红")
        elif top5_avg:
            parts.append(f"Top5均涨{_fmt_signed(top5_avg)}%")

    return f"{lead}；" + "，".join(parts[:3])


def print_report(result, top_n=5):
    """打印轮动分组结果。"""
    print("\n=== 板块轮动分组结果 ===")
    for group in ["回流优先", "升温接力", "放弃观察"]:
        items = result.get(group, [])[:top_n]
        print(f"\n{group}:")
        if not items:
            print("  - 无")
            continue
        for i, c in enumerate(items, 1):
            hot_rank = c.get("hot_rank")
            hot_text = f" 热度排名:{hot_rank}" if hot_rank else ""
            print(f"  {i}. {c['name']} 分数:{c['score']:.1f}{hot_text} 原因:{c['reason']}")
            tops = c.get("top_stocks", [])
            if tops:
                summary = "、".join(f"{s['name']} {s['change_pct']:+.2f}%" for s in tops[:3])
                print(f"     领涨个股: {summary}")


def analyze(days: int = 5, top_concepts: int = 10, top_stocks: int = 5):
    """
    收盘后板块轮动分析

    扫描全概念池，按超额收益强度序列将板块分为
    回流优先 / 升温接力 / 放弃观察 三组。
    """
    print(f"=== 热点板块轮动分析（近{days}天）===\n")

    # 1. 概念池
    print("1. 获取全市场概念池...")
    try:
        all_concepts = adata.stock.info.all_concept_code_ths()
    except Exception as e:
        print(f"获取概念池失败: {e}")
        return None
    if all_concepts is None or all_concepts.empty:
        print("获取概念池失败: 返回空数据")
        return None

    # 2. 热点榜（弱加权）
    print("2. 获取当日热点榜（仅作弱加权）...")
    try:
        hot_df = adata.sentiment.hot.hot_concept_20_ths()
    except Exception as e:
        print(f"获取热点榜失败，降级为无热点加权: {e}")
        hot_df = pd.DataFrame()

    # 构建热点索引
    hot_by_code, hot_by_name = {}, {}
    if hot_df is not None and not hot_df.empty:
        for _, row in hot_df.head(min(top_concepts, len(hot_df))).iterrows():
            print(
                f"   {int(row['rank']):2d}. {row['concept_name']:12s} "
                f"涨跌幅:{float(row['change_pct']):+.2f}% 热度:{_format_hot_tag(row.get('hot_tag'))}"
            )
        for _, row in hot_df.iterrows():
            d = row.to_dict()
            code = str(d.get("concept_code", "") or "")
            name = d.get("concept_name")
            if code:
                hot_by_code[code] = d
            if name:
                hot_by_name[name] = d

    # 3. 基准
    start_date = get_recent_date(days + 20)
    benchmark = _get_benchmark(start_date, lookback_days=days + 20)
    if not benchmark:
        print("获取沪深300基准失败: 无法计算相对强度")
        return None

    # 4. 并发加载概念历史 + 初筛评分
    print("\n3. 计算全概念轮动评分...")
    empty_feat = stock_confirm(pd.DataFrame())
    pending = []

    for cd in _fetch_concepts(all_concepts, start_date):
        if cd["error"]:
            print(f"   分析{cd['name']}失败: {cd['error']}")
            continue

        sdf = daily_strength(cd["hist"].tail(max(days, LOOKBACK)), benchmark)
        if sdf.empty:
            continue

        hot_row = _match_hot_row(cd, hot_by_code, hot_by_name)
        pr = pullback_score(sdf, hot_row, empty_feat)
        wr = warming_score(sdf, hot_row, empty_feat)

        pending.append({
            "name": cd["name"],
            "index_code": cd["index_code"],
            "concept_code": cd.get("concept_code"),
            "code": cd["index_code"],
            "hot_row": hot_row,
            "sdf": sdf, "pr": pr, "wr": wr,
        })

    # 5. 并发加载成分股确认
    stock_map = _fetch_stocks_batch(
        _pick_cands(pending, top_concepts=top_concepts), top_n=top_stocks,
    )

    # 6. 最终评分 + 分组
    results = {"回流优先": [], "升温接力": [], "放弃观察": []}

    for p in pending:
        sc = stock_map.get(p["code"])
        feat = sc["feat"] if sc else empty_feat
        tops = sc["tops"] if sc else []

        pr = pullback_score(p["sdf"], p["hot_row"], feat)
        wr = warming_score(p["sdf"], p["hot_row"], feat)

        group = classify(pull=pr["score"], warm=wr["score"], gate=pr["gate_passed"], was_strong=pr["was_strong"])

        # 放弃观察组过滤噪音：既无热度也从未走强的不收录
        if group == "放弃观察" and not p["hot_row"] and not pr["was_strong"]:
            continue

        score = pr["score"] if group == "回流优先" else wr["score"] if group == "升温接力" else max(pr["score"], wr["score"])

        results[group].append({
            "name": p["name"], "index_code": p["code"], "score": score,
            "reason": _reason(group, pr, wr, feat, p["hot_row"]),
            "pullback_score": pr["score"], "warming_score": wr["score"],
            "hot_rank": p["hot_row"].get("rank") if p["hot_row"] else None,
            "top_stocks": tops,
        })

    for group in results:
        results[group].sort(key=lambda x: x["score"], reverse=True)

    print_report(results, top_n=top_concepts)
    return results


if __name__ == "__main__":
    result = analyze(days=5, top_concepts=10, top_stocks=5)
