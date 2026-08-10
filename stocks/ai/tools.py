"""行情 tools 包装与 OpenAI function schema。"""

from __future__ import annotations

import contextlib
import copy
import datetime
import io
import json
import math
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from stocks.ai import search as ai_search
from stocks.util import env_int

_STDOUT_LOCK = threading.Lock()
_TZ = ZoneInfo("Asia/Shanghai")


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
            "as_of": _jsonable(getattr(obj, "as_of", None)),
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


def _bool_arg(args: dict, name: str, default: bool = False) -> bool:
    raw = args.get(name)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return raw


# 并行 tool 批次外层已重定向 stdout 时，内层 _quiet 跳过，避免锁串行化
_BATCH_QUIET = 0


@contextlib.contextmanager
def quiet_batch():
    """整批并行 tool 共用一次 stdout/stderr 重定向，使内层 _quiet 不再抢锁。"""
    global _BATCH_QUIET
    buf = io.StringIO()
    with (
        _STDOUT_LOCK,
        contextlib.redirect_stdout(buf),
        contextlib.redirect_stderr(buf),
    ):
        _BATCH_QUIET += 1
        try:
            yield
        finally:
            _BATCH_QUIET -= 1


@contextlib.contextmanager
def _quiet():
    """工具执行时吞掉模块输出，避免刷屏 REPL。"""
    if _BATCH_QUIET > 0:
        yield
        return
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
        "as_of": result.get("as_of"),
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
    source_dates = sorted(
        {
            str(value)[:10]
            for row in [*index_rows, *(watch_rows or [])]
            if (value := getattr(row, "as_of", None)) is not None
        }
    )
    return _ok(
        {
            "as_of": source_dates[0] if len(source_dates) == 1 else None,
            "as_of_dates": source_dates,
            "observed_at": datetime.datetime.now(_TZ).isoformat(timespec="seconds"),
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


def _tool_watch_advice(args: dict) -> dict:
    import stocks.watch_advice as watch_advice

    # 默认跳过板面重拉：日复盘应已调 mood/style/rot/hot/crowd/etf，避免双重全量扫描
    skip_board = _bool_arg(args, "skip_board", default=True)
    with _quiet():
        data = watch_advice.analyze(skip_board=skip_board)
    if data is None:
        return _err("自选取证失败")
    # 空自选也返回 ok，由模型在「自选操作建议」写明缺口
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
    "watch_advice": _tool_watch_advice,
    "web_search": _tool_web_search,
}

# 短时缓存只读行情 tools，避免同一轮复盘重复拉数；不能按整日缓存，
# 否则早盘快照会一直冒充收盘数据。
_CACHEABLE_TOOLS = frozenset(
    {"mood", "style", "rot", "crowd", "etf", "hot"}
)
_DEFAULT_CACHE_TTL_SECONDS = 300
_TOOL_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_TOOL_CACHE_DAY: str | None = None
_TOOL_CACHE_LOCK = threading.Lock()


def _cache_key(name: str, args: dict | None) -> tuple[str, str]:
    payload = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    return name, payload


def clear_tool_cache() -> None:
    """测试或跨日强制刷新。"""
    global _TOOL_CACHE_DAY
    with _TOOL_CACHE_LOCK:
        _TOOL_CACHE.clear()
        _TOOL_CACHE_DAY = None


def _should_cache(result: dict) -> bool:
    """只缓存完整成功结果；带 errors 的降级快照不冻住，便于下轮重试。"""
    if not result.get("ok"):
        return False
    data = result.get("data")
    if isinstance(data, dict) and data.get("errors"):
        return False
    return True


def call(name: str, args: dict | None = None) -> dict:
    """执行 tool，统一返回 {ok, data, error}。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        return _err(f"未知 tool: {name}")

    use_cache = name in _CACHEABLE_TOOLS
    today: str | None = None
    key: tuple[str, str] | None = None
    if use_cache:
        from stocks.util import recent_date

        today = recent_date(0)
        key = _cache_key(name, args)
        lookup_time = time.monotonic()
        ttl_seconds = env_int(
            "OMG_TOOL_CACHE_TTL_SECONDS",
            _DEFAULT_CACHE_TTL_SECONDS,
        )
        cache_hit = False
        cached_payload = None
        with _TOOL_CACHE_LOCK:
            global _TOOL_CACHE_DAY
            if _TOOL_CACHE_DAY != today:
                _TOOL_CACHE.clear()
                _TOOL_CACHE_DAY = today
            cached = _TOOL_CACHE.get(key)
            if cached is not None:
                stored_at, payload = cached
                if lookup_time - stored_at <= ttl_seconds:
                    cache_hit = True
                    cached_payload = copy.deepcopy(payload)
                else:
                    # 在同一临界区淘汰，避免并发请求刚写入的新值被误删。
                    _TOOL_CACHE.pop(key, None)
        if cache_hit:
            return cached_payload

    try:
        result = handler(args or {})
    except Exception as e:
        return _err(str(e))

    if use_cache and key is not None and today is not None and _should_cache(result):
        from stocks.util import recent_date

        with _TOOL_CACHE_LOCK:
            now = recent_date(0)
            if _TOOL_CACHE_DAY != now:
                _TOOL_CACHE.clear()
                _TOOL_CACHE_DAY = now
            # 慢 handler 跨日：丢弃写入，避免把旧日结果冻进新日桶
            if now == today:
                _TOOL_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result


def schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "mood",
                "description": "涨停四池的描述性启发式情绪标签（非预测概率）",
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
                "description": "大小盘/科技/红利相对收益的启发式风格标签（非预测）",
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
                "description": "基于超额、量能与成分宽度的启发式轮动分组（非预测）",
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
                "description": "行业交易份额的近3年历史分位；只作拥挤风险上下文，不是反转概率",
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
                "description": "主题 ETF 短/中线状态 + 量比 + 当日 session_path（高开低走/冲高回落等；非收益预测）",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "hot",
                "description": "概念板块启发式强度 Top（超额收益×成交额权重）",
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
                "description": "主要指数与自选股快照；区分各行情数据日期与请求观察时间",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rank",
                "description": "个股涨跌两端观察截面（源接口无交易日期；含标签和绝对成交额，不能据此判断放量/缩量或全市场宽度）",
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
                "name": "watch_advice",
                "description": "自选股逐票取证：默认仅技术全套快照+行业/概念标签（含 rel_vol/vol_stance 量能）；板走势/大盘请用本轮 mood/style/rot/hot/crowd/etf 对齐",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skip_board": {
                            "type": "boolean",
                            "description": "默认 true。为 false 时额外拉 rot/hot/crowd/etf/情绪风格做板面对齐（很慢，日复盘勿开）",
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


def dumps(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)
