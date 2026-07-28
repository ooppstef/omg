"""跨模块共享的轻量工具函数。"""

from __future__ import annotations

import contextlib
import datetime
import math
import os
import threading
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

_DIRECT_HTTP_LOCK = threading.RLock()
_DIRECT_HTTP_LOCAL = threading.local()
_DIRECT_HTTP_USERS = 0
_DIRECT_HTTP_ORIGINAL_REQUEST = None
_DIRECT_HTTP_TIMEOUT_SECONDS = 15
_MARKET_TZ = ZoneInfo("Asia/Shanghai")


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
    today = datetime.datetime.now(_MARKET_TZ).date()
    return (today - datetime.timedelta(days=n)).strftime("%Y-%m-%d")


def finite_float(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fmt_chg(chg) -> str:
    number = finite_float(chg)
    return f"{number:+.2f}%" if number is not None else "-"


@contextlib.contextmanager
def force_direct_http() -> Iterator[None]:
    """在当前线程内临时强制 requests 直连。

    东财等站点经本地 Clash/系统代理时常出现 ProxyError；
    crowd/etf 自建 Session 已设 trust_env=False，akshare 仍会读代理环境变量。

    仅在 enter/exit 时持锁，并用线程本地深度区分需要直连的调用；这样并发抓取
    不会互相串行，也不会改写进程环境或影响其他线程的正常代理请求。
    """
    import requests

    global _DIRECT_HTTP_ORIGINAL_REQUEST, _DIRECT_HTTP_USERS

    with _DIRECT_HTTP_LOCK:
        if _DIRECT_HTTP_USERS == 0:
            original_request = requests.Session.request
            _DIRECT_HTTP_ORIGINAL_REQUEST = original_request

            def _direct_request(self, method, url, **kwargs):
                if getattr(_DIRECT_HTTP_LOCAL, "depth", 0) > 0:
                    # 空字符串会覆盖环境变量和 Session.proxies；同时覆盖 ALL_PROXY。
                    kwargs["proxies"] = {"http": "", "https": "", "all": ""}
                    if kwargs.get("timeout") is None:
                        kwargs["timeout"] = _DIRECT_HTTP_TIMEOUT_SECONDS
                return original_request(self, method, url, **kwargs)

            requests.Session.request = _direct_request  # type: ignore[method-assign]
        _DIRECT_HTTP_USERS += 1
        _DIRECT_HTTP_LOCAL.depth = getattr(_DIRECT_HTTP_LOCAL, "depth", 0) + 1

    try:
        yield
    finally:
        with _DIRECT_HTTP_LOCK:
            _DIRECT_HTTP_LOCAL.depth -= 1
            _DIRECT_HTTP_USERS -= 1
            if _DIRECT_HTTP_USERS == 0:
                requests.Session.request = _DIRECT_HTTP_ORIGINAL_REQUEST  # type: ignore[method-assign]
                _DIRECT_HTTP_ORIGINAL_REQUEST = None
