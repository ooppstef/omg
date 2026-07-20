"""筛选入口：数据检查 → 更新 → 加载 → 算指标 → 跑策略 → 输出。"""

import stocks.filter.db as filter_db
import stocks.filter.indicator as indicator
import stocks.db as base_db
from stocks.filter.algo import STRATEGIES


def _get_name_map():
    """构建 code -> name 映射，用于输出时补充股票名称。"""
    import sqlite3

    try:
        conn = sqlite3.connect(base_db.dir_path())
        cursor = conn.execute("SELECT code, name FROM Base")
        result = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return result
    except Exception:
        return {}


def run_strategy(key):
    """执行指定策略。"""
    if key not in STRATEGIES:
        print("未知策略")
        return False

    name, func = STRATEGIES[key]
    print(f"\n=== 执行筛选: {name} ===\n")

    print("检查数据...")
    try:
        filter_db.ensure_data(progress=True)
    except RuntimeError as e:
        print(f"\n数据更新失败，筛选中止: {e}")
        return False

    print("加载日线数据...")
    all_data = filter_db.load_all(min_rows=60)
    if not all_data:
        print("无可用数据")
        return False

    name_map = _get_name_map()
    st_codes = {c for c, n in name_map.items() if "ST" in n.upper()}

    print(f"筛选中... 共 {len(all_data)} 只股票（排除 {len(st_codes)} 只ST）")
    hits = []
    for code, df in all_data.items():
        if code in st_codes:
            continue
        df = indicator.compute_all(df)
        result = func(code, df)
        if result is not None:
            result.setdefault("code", code)
            if not result.get("name"):
                result["name"] = name_map.get(code, "")
            hits.append(result)

    hits.sort(key=lambda x: float(x.get("总分", 0)), reverse=True)

    if not hits:
        print("未筛选到符合条件的股票")
        return True

    print(f"\n命中 {len(hits)} 只，展示 Top 50:\n")
    for line in _glossary_lines():
        print(line)
    print()
    _print_results(hits[:50])
    return True


def _print_results(hits):
    """格式化打印筛选结果。"""
    for i, h in enumerate(hits, 1):
        for line in _result_lines(i, h):
            print(line)


def _glossary_lines():
    """构建结果阅读提示。"""
    return [
        "读法提示:",
        "  - 总分 = 趋势背景 + 回调质量 + 触发确认；越高表示结构越完整。",
        "  - Trend / Pullback / Trigger: 分别对应 趋势 / 回调 / 触发 三段状态机。",
        "  - DIF / DEA / MACD: MACD 三件套，反映短线动量与修复节奏。",
        "  - PPO: 百分比版 MACD，适合横向比较不同价位股票。",
        "  - close_loc: 收盘在当日振幅中的位置，越高表示越接近日内强势收盘。",
        "  - 缩量比: 回调期成交量 / 上涨期成交量，越低通常表示抛压越轻。",
    ]


def _result_lines(index, hit):
    """把候选结果转成更容易阅读的多行文本。"""
    code = hit.get("code", "")
    name = hit.get("name", "")
    pattern = hit.get("形态", "")
    score = hit.get("总分", 0)
    cand_type = hit.get("候选类型", "")
    lead = f"[{pattern}]"
    if cand_type:
        lead = f"[{cand_type}/{pattern}]"

    lines = [f"  {index:>2}. {code} {name:<8s} {lead} 总分:{score}"]

    if all(k in hit for k in ("趋势分", "回调分", "触发分")):
        lines.append(
            "      评分: 趋势/回调/触发 = "
            f"{hit['趋势分']} / {hit['回调分']} / {hit['触发分']}"
        )

    price_parts = []
    for label, col in (
        ("收盘", "close"),
        ("MA10", "MA10"),
        ("MA20", "MA20"),
        ("回撤", "回撤"),
        ("缩量比", "缩量比"),
    ):
        if col in hit:
            price_parts.append(f"{label}:{hit[col]}")
    if price_parts:
        lines.append(f"      价格位置: {' | '.join(price_parts)}")

    momentum_parts = []
    for label, col in (
        ("DIF", "DIF"),
        ("DEA", "DEA"),
        ("MACD柱", "MACD"),
        ("ATR波动", "ATR"),
    ):
        if col in hit:
            momentum_parts.append(f"{label}:{hit[col]}")
    if momentum_parts:
        lines.append(f"      动量确认: {' | '.join(momentum_parts)}")

    notes = []
    if hit.get("风险说明"):
        notes.append(f"风险:{hit['风险说明']}")
    if hit.get("触发说明"):
        notes.append(f"触发:{hit['触发说明']}")
    if notes:
        lines.append(f"      {' | '.join(notes)}")

    return lines
