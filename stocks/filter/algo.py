"""筛选策略集合。

每个策略函数签名：func(code, df) -> dict | None
- code: 股票代码
- df: 已计算好指标的单只股票 DataFrame
- 返回 dict 表示命中（含展示字段），返回 None 表示未命中
"""


def strategy_placeholder_1(code, df):
    """策略1 占位 — 后续替换为实际逻辑。"""
    return None


def strategy_placeholder_2(code, df):
    """策略2 占位。"""
    return None


def strategy_placeholder_3(code, df):
    """策略3 占位。"""
    return None


def strategy_placeholder_4(code, df):
    """策略4 占位。"""
    return None


# 策略注册表：菜单序号 -> (名称, 函数)
STRATEGIES = {
    "1": ("策略1", strategy_placeholder_1),
    "2": ("策略2", strategy_placeholder_2),
    "3": ("策略3", strategy_placeholder_3),
    "4": ("策略4", strategy_placeholder_4),
}
