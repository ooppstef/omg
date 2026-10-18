"""东财题材库（主题投资）数据：今日机会 / 近期热点 / 热门主题 / 个股风向标 / 市场风格。

来源为 quote.eastmoney.com/zhuti 的匿名 GET 接口（App 版 emcfgdata 接口需要签名，未采用）。
数据是东财编辑与热度口径，只作主题研究/复盘的上下文补充，不是预测。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from stocks.util import finite_float, fmt_chg

_BASE_URL = "https://quote.eastmoney.com/zhuti/api/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_TIMEOUT_SECONDS = 15

DEFAULT_THEME_COUNT = 20
MAX_THEME_COUNT = 50
DEFAULT_STOCK_COUNT = 10
MAX_STOCK_COUNT = 30
MAX_PAGE = 100

_ORDER_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


def _session():
    import requests

    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": _UA,
            "Referer": "https://quote.eastmoney.com/zhuti/",
        }
    )
    return session


def _validate_n(name: str, value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须在 1..{maximum} 之间")
    return value


def _validate_page(name: str, value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_PAGE
    ):
        raise ValueError(f"{name} 必须在 1..{MAX_PAGE} 之间")
    return value


def _parse_value(raw: Any) -> Any:
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return None
    # 纯数字但带前导零（股票代码/分类 ID）必须保持字符串，避免 000584 → 584
    if len(text) > 1 and text[0] == "0" and text.isdigit():
        return text
    try:
        return int(text)
    except ValueError:
        pass
    number = finite_float(text)
    if number is not None:
        return number
    return text


def _parse_table(payload) -> dict:
    """解析东财 {FieldName, SplitSymbol, Data} 表格结构。"""
    if not isinstance(payload, list) or not payload:
        return {"rows": [], "total_pages": 0}
    table = payload[0] if isinstance(payload[0], dict) else {}
    fields = str(table.get("FieldName") or "").split(",")
    split_symbol = str(table.get("SplitSymbol") or "|")
    rows = []
    for line in table.get("Data") or []:
        parts = str(line).split(split_symbol)
        rows.append(
            {
                field: _parse_value(parts[index]) if index < len(parts) else None
                for index, field in enumerate(fields)
            }
        )
    try:
        total_pages = int(table.get("TotalPage") or 0)
    except (TypeError, ValueError):
        total_pages = 0
    return {"rows": rows, "total_pages": total_pages}


def _fetch(path: str, params: Optional[dict] = None) -> dict:
    import requests

    session = _session()
    try:
        resp = session.get(
            _BASE_URL + path, params=params or {}, timeout=_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"题材接口请求失败（{path}）: {exc}") from exc
    finally:
        session.close()
    if resp.status_code != 200:
        raise RuntimeError(f"题材接口 {path} 返回 HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"题材接口 {path} 返回非 JSON") from exc
    if not isinstance(payload, dict) or not payload.get("re"):
        message = payload.get("message") if isinstance(payload, dict) else ""
        raise RuntimeError(f"题材接口 {path} 返回异常: {message or 're=false'}")
    return payload


def today_opportunity() -> list[dict]:
    """今日机会（题材 + 入选理由 + 关联标的）。"""
    return _parse_table(_fetch("todayopportunity").get("result"))["rows"]


def recent_hot() -> list[dict]:
    """近期热点（题材 + 代表股）。"""
    return _parse_table(_fetch("recenthot").get("result"))["rows"]


def hot_themes(page: int = 1, page_size: int = DEFAULT_THEME_COUNT) -> dict:
    """全部题材按当日涨幅排序（含父级分类与领涨股）。"""
    page_size = _validate_n("page_size", page_size, MAX_THEME_COUNT)
    page = _validate_page("page", page)
    payload = _fetch(
        "hottheme",
        {"startIndex": (page - 1) * page_size + 1, "pageSize": page_size},
    )
    parsed = _parse_table(payload.get("result"))
    return {
        "page": page,
        "page_size": page_size,
        "total_pages": parsed["total_pages"],
        "rows": parsed["rows"],
    }


def hot_stocks(
    page: int = 1,
    page_size: int = DEFAULT_STOCK_COUNT,
    order_field: str = "Pchg",
) -> dict:
    """个股风向标（入选原因 + 现价/涨跌幅）。"""
    page_size = _validate_n("page_size", page_size, MAX_STOCK_COUNT)
    page = _validate_page("page", page)
    field = (order_field or "Pchg").strip() or "Pchg"
    if not _ORDER_FIELD_RE.match(field):
        raise ValueError("order_field 仅支持字母/数字/下划线（不超过 32 字符）")
    payload = _fetch(
        "hotstock",
        {
            "orderfield": field,
            "OrderType": 0,
            "startIndex": (page - 1) * page_size + 1,
            "pageSize": page_size,
        },
    )
    parsed = _parse_table(payload.get("result"))
    return {
        "page": page,
        "page_size": page_size,
        "order_field": field,
        "total_pages": parsed["total_pages"],
        "rows": parsed["rows"],
    }


def market_style(days: int = 1) -> dict:
    """市场风格（权重/价值/成长等分类下的题材热度）。days 支持 1/3/5。"""
    if isinstance(days, bool) or days not in (1, 3, 5):
        raise ValueError("days 仅支持 1/3/5")
    path = {1: "fenggeindex", 3: "fengge/3", 5: "fengge/5"}[days]
    payload = _fetch(path)
    result = payload.get("result") or []
    first = result[0] if result and isinstance(result[0], dict) else {}
    categories = [
        {
            "category": str(item.get("Category") or ""),
            "themes": item.get("ThemeList") or [],
        }
        for item in first.get("MarketStyle") or []
        if isinstance(item, dict)
    ]
    return {
        "days": days,
        "top_text": first.get("TopText") or None,
        "categories": categories,
    }


def print_theme_report() -> bool:
    """终端摘要：今日机会 / 近期热点 / 热门主题 / 个股风向标。"""
    printed = False

    print("\n=== 东财题材库 ===")
    try:
        rows = today_opportunity()
        print("\n--- 今日机会 ---")
        if not rows:
            print("（空）")
        for row in rows:
            code = row.get("SecuCode") or ""
            name = row.get("SecuName") or "-"
            print(
                f"{row.get('CategoryName') or '-'}  {fmt_chg(row.get('CategoryPchg'))}  "
                f"标的: {name}({code})"
            )
            reason = str(row.get("Reason") or "").strip()
            if reason:
                print(f"    理由: {reason}")
        printed = printed or bool(rows)
    except Exception as exc:
        print(f"今日机会获取失败: {exc}")

    try:
        rows = recent_hot()
        print("\n--- 近期热点 ---")
        if not rows:
            print("（空）")
        for row in rows:
            code = row.get("SecuCode") or ""
            name = row.get("SecuName") or "-"
            print(f"{row.get('CategoryName') or '-'}  代表股: {name}({code})")
        printed = printed or bool(rows)
    except Exception as exc:
        print(f"近期热点获取失败: {exc}")

    try:
        data = hot_themes(page_size=DEFAULT_THEME_COUNT)
        print(f"\n--- 热门主题 Top{len(data['rows'])}（按当日涨幅）---")
        if not data["rows"]:
            print("（空）")
        for index, row in enumerate(data["rows"], 1):
            parent = row.get("ParentName") or ""
            parent_part = f"（{parent}）" if parent else ""
            code = row.get("SecuCode") or ""
            name = row.get("SecuName") or "-"
            print(
                f"{index}. {row.get('CategoryName') or '-'}{parent_part}  "
                f"{fmt_chg(row.get('CategoryPchg'))}  领涨: {name}({code})"
            )
        printed = printed or bool(data["rows"])
    except Exception as exc:
        print(f"热门主题获取失败: {exc}")

    try:
        data = hot_stocks(page_size=DEFAULT_STOCK_COUNT)
        print(f"\n--- 个股风向标 Top{len(data['rows'])} ---")
        if not data["rows"]:
            print("（空）")
        for index, row in enumerate(data["rows"], 1):
            reason = str(row.get("MainPointCon") or "").strip()
            reason = reason[:60] + ("…" if len(reason) > 60 else "")
            price = finite_float(row.get("CurrentPrice"))
            price_text = f"{price:g}" if price is not None else "-"
            print(
                f"{index}. {row.get('SecuName') or '-'}({row.get('SecuCode') or ''})  "
                f"{fmt_chg(row.get('Pchg'))}  现价 {price_text}"
            )
            if reason:
                print(f"    入选原因: {reason}")
        printed = printed or bool(data["rows"])
    except Exception as exc:
        print(f"个股风向标获取失败: {exc}")

    return printed
