"""跨模块共享的轻量工具函数。"""

from __future__ import annotations

import datetime
import math
import os
from typing import Optional


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def recent_date(n: int) -> str:
    today = datetime.date.today()
    return (today - datetime.timedelta(days=n)).strftime("%Y-%m-%d")


def finite_float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fmt_chg(chg) -> str:
    if isinstance(chg, (int, float)) and chg == chg:
        return f"{chg:+.2f}%"
    return "-"
