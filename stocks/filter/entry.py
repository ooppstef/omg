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
        return

    name, func = STRATEGIES[key]
    print(f"\n=== 执行筛选: {name} ===\n")

    # 1. 确保数据最新
    try:
        filter_db.ensure_data(progress=True)
    except RuntimeError as e:
        print(f"\n数据更新失败，筛选中止: {e}")
        return

    # 2. 加载全市场日线
    print("加载日线数据...")
    all_data = filter_db.load_all(min_rows=30)
    if not all_data:
        print("无可用数据")
        return

    # 3. 构建名称映射
    name_map = _get_name_map()

    # 4. 逐只计算指标 + 跑策略
    print(f"筛选中... 共 {len(all_data)} 只股票")
    hits = []
    for code, df in all_data.items():
        df = indicator.compute_all(df)
        result = func(code, df)
        if result is not None:
            result.setdefault("code", code)
            if not result.get("name"):
                result["name"] = name_map.get(code, "")
            hits.append(result)

    # 5. 按总分排序
    hits.sort(key=lambda x: float(x.get("总分", 0)), reverse=True)

    # 6. 输出
    if not hits:
        print("未筛选到符合条件的股票")
        return

    print(f"\n命中 {len(hits)} 只:\n")
    _print_results(hits)


def _print_results(hits):
    """格式化打印筛选结果。"""
    for i, h in enumerate(hits, 1):
        # 第一行：序号 + 代码 + 名称 + 形态 + 总分
        code = h.get("code", "")
        stock_name = h.get("name", "")
        pattern = h.get("形态", "")
        score = h.get("总分", 0)
        candidate_type = h.get("候选类型", "")
        lead = f"[{pattern}]"
        if candidate_type:
            lead = f"[{candidate_type}/{pattern}]"
        print(f"  {i:>2}. {code} {stock_name:<8s} {lead} 总分:{score}")

        # 第二行：关键指标
        parts = []
        for col in ["close", "MA10", "MA20", "回撤", "缩量比"]:
            if col in h:
                parts.append(f"{col}:{h[col]}")
        print(f"      {' | '.join(parts)}")

        # 第三行：MACD + ATR
        macd_parts = []
        for col in ["DIF", "DEA", "MACD", "ATR"]:
            if col in h:
                macd_parts.append(f"{col}:{h[col]}")
        if macd_parts:
            print(f"      {' | '.join(macd_parts)}")

        # 第四行：风险与触发说明
        explain_parts = []
        for col in ["风险说明", "触发说明"]:
            if h.get(col):
                explain_parts.append(f"{col}:{h[col]}")
        if explain_parts:
            print(f"      {' | '.join(explain_parts)}")
