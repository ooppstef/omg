"""OpenAI 兼容 client 与环境变量配置。"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_LOADED = False


class ConfigError(RuntimeError):
    """LLM 配置缺失或无效。"""


def _load_dotenv() -> None:
    """加载仓库根目录 .env；已存在的环境变量优先，不被覆盖。"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    # 测试或显式禁用时跳过，避免 clear=True 后被 .env 重新注入
    if (os.getenv("OMG_DISABLE_DOTENV") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return
    path = Path(__file__).resolve().parents[2] / ".env"
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


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ConfigError(
            f"缺少环境变量 {name}。请设置 OMG_LLM_BASE_URL / OMG_LLM_API_KEY / OMG_LLM_MODEL"
        )
    return value


def settings() -> dict:
    _load_dotenv()
    raw_timeout = (os.getenv("OMG_LLM_TIMEOUT") or "60").strip()
    try:
        timeout = int(raw_timeout)
    except ValueError as e:
        raise ConfigError("OMG_LLM_TIMEOUT 必须是 1..600 之间的整数") from e
    if not 1 <= timeout <= 600:
        raise ConfigError("OMG_LLM_TIMEOUT 必须在 1..600 秒之间")
    return {
        "base_url": _require("OMG_LLM_BASE_URL"),
        "api_key": _require("OMG_LLM_API_KEY"),
        "model": _require("OMG_LLM_MODEL"),
        "timeout": timeout,
    }


def client():
    """构造 OpenAI 兼容 client；缺配置时抛 ConfigError。"""
    from openai import OpenAI

    cfg = settings()
    return OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        timeout=cfg["timeout"],
        max_retries=0,
    ), cfg["model"]
