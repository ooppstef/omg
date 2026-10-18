"""问财（同花顺）自然语言查询：匿名选股/取证，供 chat tool 与 CLI 复用。

链路（当前问财匿名可用）：
1. 取问财首页 chameleon 脚本里的 TOKEN_SERVER_TIME（服务端时间种子）；
2. 本地用 adata 随包 hexin.js（py-mini-racer 执行）计算 hexin-v 请求头；
3. POST unifiedwap/unified-wap/v2/result/get-robot-data（表单），解析
   answer 里 xuangu_table 组件的 columns/datas。

说明：问财 2025 起收紧过策略（部分入口需登录 Cookie）。如遇 403/登录墙，
可设置 OMG_WENCAI_COOKIE（浏览器登录后复制 Cookie 值）作为兜底。
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

_INDEX_URL = "https://www.iwencai.com/unifiedwap/home/index"
_ROBOT_URL = "https://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_TIMEOUT_SECONDS = 20
MAX_PERPAGE = 100
_TOKEN_TTL_SECONDS = 600

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}

# 结果渲染时隐藏的列（问财表格里的辅助字段）
_HIDDEN_COLUMNS = {"market_code"}


def _session():
    import requests

    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": _UA,
            "Referer": "https://www.iwencai.com/unifiedwap/result",
        }
    )
    return session


def _validate_page(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} 必须为正整数")
    return value


# ---------------------------------------------------------------- token

def _server_time(session) -> str:
    """从问财首页的 chameleon 脚本里抽取 TOKEN_SERVER_TIME。"""
    import requests

    try:
        resp = session.get(_INDEX_URL, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RuntimeError(f"问财首页请求失败: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"问财首页返回 HTTP {resp.status_code}")
    script_srcs = re.findall(r'<script[^>]*src="([^"]+)"', resp.text)
    for src in script_srcs:
        if not (src.startswith("http") or src.startswith("//")):
            continue
        url = src if src.startswith("http") else f"https:{src}"
        try:
            js = session.get(url, timeout=_TIMEOUT_SECONDS).text
        except Exception:
            continue
        match = re.search(r"TOKEN_SERVER_TIME\s*=\s*([0-9]+(?:\.[0-9]+)?)", js)
        if match:
            return match.group(1)
    raise RuntimeError("未能在问财首页脚本中找到 TOKEN_SERVER_TIME")


def _hexin_js_path() -> Path:
    import adata

    path = Path(adata.__file__).resolve().parent / "common" / "js" / "hexin.js"
    if not path.exists():
        raise RuntimeError(
            "adata 内置 hexin.js 不存在（升级 adata 后重试），"
            "或设置 OMG_WENCAI_COOKIE 走 Cookie 兜底"
        )
    return path


def _compute_token() -> str:
    """本地计算 hexin-v；异常统一转成可读的 RuntimeError。"""
    try:
        from py_mini_racer import MiniRacer
    except ImportError as exc:
        raise RuntimeError(
            "缺少 py-mini-racer，无法本地计算 hexin-v（pip install py-mini-racer），"
            "或设置 OMG_WENCAI_COOKIE 走 Cookie 兜底"
        ) from exc

    session = _session()
    try:
        server_time = _server_time(session)
    finally:
        session.close()

    code = (
        f"var TOKEN_SERVER_TIME={server_time};\n"
        + _hexin_js_path().read_text(encoding="utf-8")
    )
    try:
        ctx = MiniRacer()
        ctx.eval(code)
        token = ctx.call("rt.updata")
    except Exception as exc:
        raise RuntimeError(f"hexin-v 计算失败: {exc}") from exc
    if not token or not isinstance(token, str):
        raise RuntimeError("hexin-v 计算失败：返回为空")
    return token.strip()


def _hexin_token() -> str:
    now = time.monotonic()
    with _TOKEN_LOCK:
        token = _TOKEN_CACHE.get("token")
        expires_at = float(_TOKEN_CACHE.get("expires_at") or 0.0)
        if token and now < expires_at:
            return token
    token = _compute_token()
    with _TOKEN_LOCK:
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = time.monotonic() + _TOKEN_TTL_SECONDS
    return token


# ---------------------------------------------------------------- request

def _form(question: str, secondary_intent: str, page: int, perpage: int) -> dict:
    return {
        "question": question,
        "source": "Ths_iwencai_Xuangu",
        "version": "2.0",
        "secondary_intent": secondary_intent,
        "perpage": str(perpage),
        "page": str(page),
        "add_info": (
            '{"urp":{"scene":1,"company":1,"business":1},'
            '"contentType":"json","searchInfo":true}'
        ),
        "log_info": '{"input_type":"typewrite"}',
    }


def _post_form(form: dict, cookie: Optional[str] = None) -> dict:
    import requests

    session = _session()
    headers = {"hexin-v": _hexin_token()}
    if cookie:
        headers["Cookie"] = cookie
    try:
        resp = session.post(
            _ROBOT_URL, data=form, headers=headers, timeout=_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"问财请求失败: {exc}") from exc
    finally:
        session.close()

    if resp.status_code == 403:
        raise RuntimeError(
            "问财拒绝访问（403）：可能触发限流或登录策略，"
            "可设置 OMG_WENCAI_COOKIE（浏览器登录后复制 Cookie）"
        )
    if resp.status_code != 200:
        raise RuntimeError(f"问财返回 HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError("问财返回非 JSON（可能被登录墙拦截）") from exc


# ---------------------------------------------------------------- parse

def _parse_robot_payload(payload: dict) -> dict:
    """从 get-robot-data 响应里取第一个 xuangu_table 组件。"""
    if not isinstance(payload, dict):
        raise RuntimeError("问财返回结构异常")
    status = payload.get("status_code")
    if status not in (0, "0"):
        message = payload.get("status_msg") or payload.get("message") or ""
        raise RuntimeError(f"问财返回异常（{status}）：{message}")

    data = payload.get("data") or {}
    answers = data.get("answer") or []
    answer = answers[0] if answers else {}
    if not isinstance(answer, dict):
        answer = {}
    text_answer = str(answer.get("text_answer") or "")

    columns: list[str] = []
    rows: list[dict] = []
    for item in answer.get("txt") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or {}
        if not isinstance(content, dict):
            continue
        for component in content.get("components") or []:
            if not isinstance(component, dict):
                continue
            show_type = str(component.get("show_type") or "")
            if not show_type.startswith("xuangu_table"):
                continue
            table = component.get("data") or {}
            if not isinstance(table, dict):
                continue
            datas = table.get("datas") or []
            if not isinstance(datas, list) or not datas or rows:
                continue
            columns = [
                str(column.get("key"))
                for column in table.get("columns") or []
                if isinstance(column, dict) and column.get("key")
            ]
            rows = [row for row in datas if isinstance(row, dict)]

    return {
        "text_answer": text_answer,
        "columns": columns,
        "rows": rows,
        "count": len(rows),
    }


# ---------------------------------------------------------------- public

def _resolve_cookie(cookie: Optional[str]) -> Optional[str]:
    """显式参数 > 环境变量（含 .env）；都没有则匿名。"""
    value = (cookie or "").strip()
    if value:
        return value
    from stocks.util import load_dotenv

    load_dotenv()
    return (os.getenv("OMG_WENCAI_COOKIE") or "").strip() or None


def query(
    question: str,
    *,
    secondary_intent: str = "stock",
    page: int = 1,
    perpage: int = MAX_PERPAGE,
    cookie: Optional[str] = None,
) -> dict:
    """执行一条问财查询，返回结构化结果。

    secondary_intent 取值与问财页面一致：stock/zhishu/fund/hkstock/usstock 等。
    """
    text = (question or "").strip()
    if not text:
        raise ValueError("question 不能为空")
    page = _validate_page("page", page)
    if (
        isinstance(perpage, bool)
        or not isinstance(perpage, int)
        or not 1 <= perpage <= MAX_PERPAGE
    ):
        raise ValueError(f"perpage 必须在 1..{MAX_PERPAGE} 之间")

    payload = _post_form(
        _form(text, secondary_intent, page, perpage),
        cookie=_resolve_cookie(cookie),
    )
    parsed = _parse_robot_payload(payload)
    return {
        "question": text,
        "secondary_intent": secondary_intent,
        "page": page,
        "perpage": perpage,
        **parsed,
    }


_DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
_COL_WIDTH = 16


def _format_number(value: float) -> str:
    formatted = f"{value:g}"
    # :g 溢出为科学计数法时（大数或临界舍入），退回千分位整数
    if "e" in formatted or "E" in formatted:
        return f"{value:,.0f}"
    return formatted


def _fmt_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return _format_number(value)
    if isinstance(value, (list, dict)):
        return "(数据)"
    text = str(value)
    # 带小数的数字串（问财原样返回）压到 6 位有效数字；纯整数串一律保留原样（代码等）
    if len(text) > 6 and _DECIMAL_RE.match(text):
        return _format_number(float(text))
    return text


def _display_width(text: str) -> int:
    """终端显示宽度：东亚宽/全角字符按 2 列计。"""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in str(text)
    )


def _fit(text: str, width: int) -> str:
    """按显示宽度截断（超长加省略号）。"""
    text = str(text or "")
    if _display_width(text) <= width:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + char_width > width - 1:
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def print_query_result(result: dict, max_rows: int = 30, max_cols: int = 8) -> None:
    """终端渲染：摘要 + 前若干列/行的对齐表格。"""
    text_answer = str(result.get("text_answer") or "").strip()
    if text_answer:
        print(text_answer)

    # 隐藏辅助列与列表型「明细」列（截断后无信息量，且挤占列位）
    columns = [
        column
        for column in result.get("columns") or []
        if column and column not in _HIDDEN_COLUMNS and "明细" not in str(column)
    ]
    rows = result.get("rows") or []
    if not rows:
        if not text_answer:
            print("（问财未返回表格数据）")
        return

    display_cols = columns[:max_cols]
    if not display_cols:
        return

    shown_rows = rows[:max_rows]
    headers = [_fit(column, _COL_WIDTH) for column in display_cols]
    widths = []
    for index, column in enumerate(display_cols):
        width = _display_width(headers[index])
        for row in shown_rows:
            width = max(
                width, _display_width(_fit(_fmt_cell(row.get(column)), _COL_WIDTH))
            )
        widths.append(min(width, _COL_WIDTH))

    print("  ".join(_pad(header, width) for header, width in zip(headers, widths)))
    for row in shown_rows:
        cells = [
            _pad(_fit(_fmt_cell(row.get(column)), _COL_WIDTH), width)
            for column, width in zip(display_cols, widths)
        ]
        print("  ".join(cells))
    if len(rows) > len(shown_rows):
        print(f"... 共 {len(rows)} 行")
