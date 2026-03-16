import adata
import datetime
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

# 常量配置
WARM_THRESH = 50
PULL_THRESH = 55
STRONG_MIN = 1.5
LOOKBACK = 8
RECENT_DAYS = 2
WARM_DAYS = 3


def _env_int(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


CONCEPT_WORKERS = _env_int("OMG_ROT_CONCEPT_WORKERS", 20)
STOCK_WORKERS = _env_int("OMG_ROT_STOCK_WORKERS", 20)


def get_recent_date(n):
    """获取n天前的日期"""
    today = datetime.date.today()
    return (today - datetime.timedelta(days=n)).strftime("%Y-%m-%d")


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
    """回流优先分数：前强后调 = 40%前强 + 30%回调质量 + 20%成分股 + 10%热度。"""
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

    score = (
        _clamp(strong_days / 2.0 * 70.0 + max_s / 5.0 * 30.0) * 0.4
        + _clamp(100.0 - abs(recent_mean + 0.4) * 80.0 - max(abs(recent_min) - 2.0, 0.0) * 20.0) * 0.3
        + _stock_score(feat) * 0.2
        + _heat_score(hot_row) * 0.1
    )

    gate = was_strong and -1.5 < recent_mean <= 0.3 and recent_min > -3.0

    return {
        "score": round(_clamp(score), 2),
        "gate_passed": gate,
        "was_strong": was_strong,
        "strong_days": strong_days,
        "recent_mean": round(recent_mean, 2),
    }


def warming_score(strength_df, hot_row, feat):
    """升温接力分数：45%斜率 + 20%量能 + 15%热度 + 20%成分股。"""
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

    score = slope_s * 0.45 + amt_s * 0.20 + _heat_score(hot_row) * 0.15 + _stock_score(feat) * 0.20
    return {"score": round(_clamp(score), 2), "slope": round(slope, 2)}


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

    def fetch_one(row):
        name = row.get("name")
        code = str(row.get("index_code") or row.get("concept_code") or "")
        if not name or not code:
            return None
        try:
            df = adata.stock.market.get_market_concept_ths(index_code=code)
        except Exception as e:
            return {"name": name, "code": code, "hist": None, "error": e}
        if df is None or df.empty:
            return None
        df = df.copy()
        df["trade_date"] = df["trade_date"].astype(str).str[:10]
        df = df[df["trade_date"] >= start_date].sort_values("trade_date")
        if len(df) < 3:
            return None
        return {"name": name, "code": code, "hist": df, "error": None}

    loaded = []
    with ThreadPoolExecutor(max_workers=CONCEPT_WORKERS) as executor:
        futures = [executor.submit(fetch_one, row) for _, row in all_concepts.iterrows()]
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
    parts = []
    if group == "回流优先":
        parts.append("前强后调")
        if hot_row:
            parts.append("热度仍有残留")
        pos = float(feat.get("positive_ratio", 0.0) or 0.0)
        parts.append("龙头承接仍在" if pos >= 0.4 else "板块内部确认一般")
    elif group == "升温接力":
        parts.append("近3日强度抬升")
        if hot_row:
            parts.append("热度边际改善")
        pos = float(feat.get("positive_ratio", 0.0) or 0.0)
        if pos >= 0.5:
            parts.append("成分股扩散较好")
    else:
        if pull_res.get("was_strong"):
            parts.append("前强基础仍在")
        parts.append("结构退化，暂不跟踪")
    return "，".join(parts)


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
                f"涨跌幅:{float(row['change_pct']):+.2f}% 热度:{row['hot_tag']}"
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

        hot_row = hot_by_code.get(cd["code"]) or hot_by_name.get(cd["name"])
        pr = pullback_score(sdf, hot_row, empty_feat)
        wr = warming_score(sdf, hot_row, empty_feat)

        pending.append({
            "name": cd["name"], "code": cd["code"], "hot_row": hot_row,
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
