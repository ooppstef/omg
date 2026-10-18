"""跨模块共享的轻量工具函数。"""

from __future__ import annotations

import contextlib
import datetime
import math
import os
import sys
import threading
from pathlib import Path
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

_DIRECT_HTTP_LOCK = threading.RLock()
_DIRECT_HTTP_LOCAL = threading.local()
_DIRECT_HTTP_USERS = 0
_DIRECT_HTTP_ORIGINAL_REQUEST = None
_DIRECT_HTTP_TIMEOUT_SECONDS = 15
_MARKET_TZ = ZoneInfo("Asia/Shanghai")
_DOTENV_LOADED = False


def load_dotenv() -> None:
    """加载仓库根目录 .env；已存在的环境变量优先，不被覆盖。

    测试或显式禁用（OMG_DISABLE_DOTENV）时跳过，避免 clear=True 后被 .env 重新注入。
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    if (os.getenv("OMG_DISABLE_DOTENV") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')


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


def supports_color(stream=None) -> bool:
    """输出流是否支持 ANSI 颜色（tty 且未设置 NO_COLOR）。

    管道/文件/日志重定向下返回 False，避免污染结构化输出；
    tools 的 stdout 重定向（StringIO）也会自然走无色路径。
    """
    if "NO_COLOR" in os.environ:
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def fmt_chg(chg) -> str:
    number = finite_float(chg)
    return f"{number:+.2f}%" if number is not None else "-"


@contextlib.contextmanager
def force_direct_http() -> Iterator[None]:
    """在当前线程内临时强制 requests 直连。

    东财等站点经本地 Clash/系统代理时常出现 ProxyError。

    职责边界（项目唯一的全局 requests patch）：
    - 仅用于第三方库内部请求（adata/akshare 的 requests 无法参数级控制），
      例如 mood 的东财涨停四池、watch_advice 的新浪日线。
    - 自建 Session（crowd._session / etf._em_session / rank._em_spot_session）
      一律 trust_env=False 直连，不需要也不应再包此 patch。
    - 不要在自建 Session 的调用上再套本 contextmanager，双轨冗余。

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
