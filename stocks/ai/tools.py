"""行情 tools 包装与 OpenAI function schema。"""

from __future__ import annotations

import contextlib
import io
import json
import math
import threading
from typing import Any, Callable

from stocks.ai import search as ai_search
from stocks.util import env_int

_STDOUT_LOCK = threading.Lock()


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return _jsonable(obj.item())
    except Exception:
        pass
    try:
        import pandas as pd

        if obj is pd.NA or obj is pd.NaT:
            return None
    except Exception:
        pass
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            pass
    # StockCurrentPrice 等简单对象
    if all(hasattr(obj, a) for a in ("code", "name", "price", "change_percent")):
        return {
            "code": obj.code,
            "name": obj.name,
            "price": _jsonable(obj.price),
            "change_percent": _jsonable(obj.change_percent),
            "amount": _jsonable(getattr(obj, "amount", None)),
        }
    try:
        import pandas as pd

        if isinstance(obj, (pd.DataFrame, pd.Series)):
            return None
    except Exception:
        pass
    return str(obj)


def _ok(data: Any) -> dict:
    return {"ok": True, "data": _jsonable(data), "error": None}


def _err(msg: str) -> dict:
    return {"ok": False, "data": None, "error": str(msg)}


def _int_arg(args: dict, name: str, default: int, maximum: int) -> int:
    raw = args.get(name)
    if raw is None:
        value = default
    elif isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{name} 必须是整数")
    else:
        value = raw
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须在 1..{maximum} 之间")
    return value


@contextlib.contextmanager
def _quiet():
    """工具执行时吞掉模块输出，避免刷屏 REPL。"""
    buf = io.StringIO()
    with (
        _STDOUT_LOCK,
        contextlib.redirect_stdout(buf),
        contextlib.redirect_stderr(buf),
    ):
        yield


def _tool_mood(args: dict) -> dict:
    import stocks.mood as mood

    days = _int_arg(
        args, "days", env_int("OMG_MOOD_DAYS", mood.DEFAULT_DAYS), 30
    )
    with _quiet():
        data = mood.analyze(days=days)
    if not data:
        return _err("无可用情绪数据")
    payload = []
    for row in data:
        item = dict(row)
        item.pop("pools", None)
        payload.append(item)
    return _ok(payload)


def _tool_style(args: dict) -> dict:
    import stocks.style as style

    days = _int_arg(
        args, "days", env_int("OMG_STYLE_DAYS", style.DEFAULT_DAYS), 30
    )
    with _quiet():
        result = style.analyze(days=days)
    feat = dict(result.get("features") or {})
    feat.pop("frame", None)
    payload = {
        "state": result.get("state"),
        "reasons": result.get("reasons"),
        "trend": result.get("trend"),
        "features": feat,
    }
    if not feat.get("ok"):
        return _err("风格数据不足或拉取失败")
    return _ok(payload)


def _tool_rot(args: dict) -> dict:
    import stocks.rot as rot

    days = _int_arg(args, "days", 5, 30)
    top_concepts = _int_arg(args, "top_concepts", 10, 30)
    top_stocks = _int_arg(args, "top_stocks", 5, 10)
    with _quiet():
        data = rot.analyze(days=days, top_concepts=top_concepts, top_stocks=top_stocks)
    if not data or not any(data.values()):
        return _err("轮动分析失败或无数据")
    return _ok(data)


def _tool_crowd(args: dict) -> dict:
    import stocks.crowd as crowd

    top = _int_arg(args, "top", 15, 50)
    with _quiet():
        data = crowd.scan_concept_crowding(top=top)
    if not data:
        return _err("无可用拥挤度数据")
    return _ok(data)


def _tool_etf(args: dict) -> dict:
    import stocks.etf as etf

    with _quiet():
        data = etf.scan_etf_states()
    if not data:
        return _err("无可用 ETF 状态数据")
    return _ok(data)


def _tool_hot(args: dict) -> dict:
    import stocks.ana as ana

    days = _int_arg(args, "days", 10, 30)
    top = _int_arg(args, "top", 10, 30)
    with _quiet():
        data = ana.concept_strength_top10(days=days, top=top, print_result=False)
    if not data:
        return _err("热点强度计算失败或无数据")
    return _ok(data)


def _tool_list(_args: dict) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    import stocks.market as market
    import stocks.watch_list as watch_list

    indexes = [
        ("000001", "上证指数"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
    ]
    errors = []

    def fetch_index(item):
        try:
            row = market.get_index_current_price(item[0], item[1])
            return (row, None) if row is not None else (None, f"{item[1]}: 无数据")
        except Exception as e:
            return None, f"{item[1]}: {e}"

    with _quiet():
        with ThreadPoolExecutor(max_workers=3) as ex:
            index_results = list(ex.map(fetch_index, indexes))
        try:
            watch_rows = watch_list.get_watch_list_current_price(print_result=False)
        except Exception as e:
            watch_rows = []
            errors.append(f"自选行情: {e}")
    index_rows = []
    for row, error in index_results:
        if row is not None:
            index_rows.append(row)
        elif error:
            errors.append(error)
    if not index_rows and not watch_rows:
        detail = "；".join(errors)
        return _err(f"主要指数与自选行情均获取失败{f'：{detail}' if detail else ''}")
    return _ok(
        {
            "indexes": index_rows,
            "watchlist": watch_rows or [],
            "errors": errors,
        }
    )


def _tool_web_search(args: dict) -> dict:
    query = args.get("query") or ""
    max_results = args.get("max_results")
    try:
        data = ai_search.web_search(query, max_results=max_results)
    except Exception as e:
        return _err(str(e))
    return _ok(data)


def _tool_rank(args: dict) -> dict:
    import stocks.rank as rank

    gainers = _int_arg(args, "gainers", rank.DEFAULT_GAINERS, rank.MAX_GAINERS)
    losers = _int_arg(args, "losers", rank.DEFAULT_LOSERS, rank.MAX_LOSERS)
    with _quiet():
        data = rank.analyze(gainers=gainers, losers=losers)
    if not data or (not data.get("gainers") and not data.get("losers")):
        return _err("无可用涨跌幅榜数据")
    return _ok(data)


_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "mood": _tool_mood,
    "style": _tool_style,
    "rot": _tool_rot,
    "crowd": _tool_crowd,
    "etf": _tool_etf,
    "hot": _tool_hot,
    "list": _tool_list,
    "rank": _tool_rank,
    "web_search": _tool_web_search,
}


def schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "mood",
                "description": "市场情绪阶段（涨停四池：冰点/修复/主升/分歧/退潮等）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "近 N 日趋势，可选",
                            "minimum": 1,
                            "maximum": 30,
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "style",
                "description": "风格结构（大小盘/科技/红利相对强弱）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "近 N 日表格，可选",
                            "minimum": 1,
                            "maximum": 30,
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rot",
                "description": "热点板块轮动（回流优先/升温接力/放弃观察）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "默认 5",
                            "minimum": 1,
                            "maximum": 30,
                        },
                        "top_concepts": {
                            "type": "integer",
                            "description": "默认 10",
                            "minimum": 1,
                            "maximum": 30,
                        },
                        "top_stocks": {
                            "type": "integer",
                            "description": "默认 5",
                            "minimum": 1,
                            "maximum": 10,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "crowd",
                "description": "东财行业交易拥挤度（近3年成交额占比分位）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "top": {
                            "type": "integer",
                            "description": "返回前 N，默认 15",
                            "minimum": 1,
                            "maximum": 50,
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "etf",
                "description": "主题 ETF 短线/中线状态与点评",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "hot",
                "description": "概念板块强度 Top（超额收益×成交额加权）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "默认 10",
                            "minimum": 1,
                            "maximum": 30,
                        },
                        "top": {
                            "type": "integer",
                            "description": "默认 10",
                            "minimum": 1,
                            "maximum": 30,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list",
                "description": "主要指数与自选股实时快照",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rank",
                "description": "个股涨幅 Top / 跌幅 Top（含行业与热点概念标签）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "gainers": {
                            "type": "integer",
                            "description": "涨幅榜条数，默认 50",
                            "minimum": 1,
                            "maximum": 100,
                        },
                        "losers": {
                            "type": "integer",
                            "description": "跌幅榜条数，默认 20",
                            "minimum": 1,
                            "maximum": 50,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "网页搜索（新闻/公告等外部信息）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "max_results": {
                            "type": "integer",
                            "description": "条数上限，可选",
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    ]


def call(name: str, args: dict | None = None) -> dict:
    """执行 tool，统一返回 {ok, data, error}。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        return _err(f"未知 tool: {name}")
    try:
        return handler(args or {})
    except Exception as e:
        return _err(str(e))


def dumps(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)
