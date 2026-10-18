"""行情数据清洗基础原语。

etf / crowd / watch_advice 对 adata / akshare / 东财原始输出的日期规范化、
数值清洗存在重复实现；此处收敛纯基础原语，各模块保留自己特有的字段约束
与口径（如 change_pct 恢复、code 过滤、amount/turnover 缺省语义）。
"""

from __future__ import annotations

import math

import pandas as pd


def parse_date_series(series) -> pd.Series:
    """解析日期序列（errors=coerce），无效日期保留为 NaT 位置。"""
    return pd.to_datetime(series, errors="coerce", format="mixed")


def clean_numeric(series) -> pd.Series:
    """数值化并清除 inf/NaN；不可转数值的变 NaN。"""
    out = pd.to_numeric(series, errors="coerce")
    return out.replace([math.inf, -math.inf], pd.NA)
