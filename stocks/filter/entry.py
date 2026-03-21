"""筛选入口：数据检查 → 更新 → 加载 → 算指标 → 跑策略 → 输出。"""

import stocks.filter.db as filter_db
import stocks.filter.indicator as indicator
from stocks.filter.algo import STRATEGIES


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

    # 3. 逐只计算指标 + 跑策略
    print(f"筛选中... 共 {len(all_data)} 只股票")
    hits = []
    for code, df in all_data.items():
        df = indicator.compute_all(df)
        result = func(code, df)
        if result is not None:
            result.setdefault("code", code)
            hits.append(result)

    # 4. 输出
    if not hits:
        print("未筛选到符合条件的股票")
        return

    print(f"\n命中 {len(hits)} 只:\n")
    for h in hits:
        parts = [f"{h.get('code', '')} {h.get('name', '')}"]
        for k, v in h.items():
            if k not in ("code", "name"):
                parts.append(f"{k}:{v}")
        print("  ".join(parts))
