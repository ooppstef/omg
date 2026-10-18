"""OpenAI 兼容 client 与环境变量配置。"""

from __future__ import annotations

import os

from stocks.util import load_dotenv


class ConfigError(RuntimeError):
    """LLM 配置缺失或无效。"""


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ConfigError(
            f"缺少环境变量 {name}。请设置 OMG_LLM_BASE_URL / OMG_LLM_API_KEY / OMG_LLM_MODEL"
        )
    return value


def settings() -> dict:
    load_dotenv()
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
