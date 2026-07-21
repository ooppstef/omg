"""跨模块共享的轻量工具函数。"""

from __future__ import annotations

import contextlib
import datetime
import math
import os
import threading
from typing import Iterator, Optional

_PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
_DIRECT_HTTP_LOCK = threading.RLock()


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


@contextlib.contextmanager
def force_direct_http() -> Iterator[None]:
    """临时禁用环境代理，并强制 requests 直连。

    东财等站点经本地 Clash/系统代理时常出现 ProxyError；
    crowd/etf 自建 Session 已设 trust_env=False，akshare 仍会读代理环境变量。

    全局 monkeypatch / 改环境变量非线程安全，用 RLock 串行化整段 enter/exit。
    """
    import requests

    with _DIRECT_HTTP_LOCK:
        saved_env = {
            key: os.environ.pop(key) for key in _PROXY_ENV_KEYS if key in os.environ
        }
        saved_no_proxy = {
            key: os.environ[key]
            for key in ("NO_PROXY", "no_proxy")
            if key in os.environ
        }
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"

        original_request = requests.Session.request

        def _direct_request(self, method, url, **kwargs):
            # 显式关闭代理，避免 trust_env / 残留 Session.proxies 再走本地代理
            kwargs["proxies"] = {"http": None, "https": None}
            return original_request(self, method, url, **kwargs)

        requests.Session.request = _direct_request  # type: ignore[method-assign]
        try:
            yield
        finally:
            requests.Session.request = original_request  # type: ignore[method-assign]
            for key in ("NO_PROXY", "no_proxy"):
                os.environ.pop(key, None)
            os.environ.update(saved_no_proxy)
            os.environ.update(saved_env)
