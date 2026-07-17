"""网页搜索（默认 ddgs）。"""

from __future__ import annotations

import os


def web_search(query: str, max_results: int | None = None) -> list[dict]:
    """返回 [{title, href, body}, ...]。"""
    query = (query or "").strip()
    if not query:
        raise ValueError("query 不能为空")

    backend = (os.getenv("OMG_SEARCH_BACKEND") or "ddgs").strip().lower()
    if backend != "ddgs":
        raise ValueError(f"不支持的搜索后端: {backend}（当前仅支持 ddgs）")

    if max_results is None:
        raw = (os.getenv("OMG_SEARCH_MAX_RESULTS") or "5").strip()
        try:
            max_results = int(raw)
        except ValueError as e:
            raise ValueError("OMG_SEARCH_MAX_RESULTS 必须是整数") from e
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ValueError("max_results 必须是整数")
    if not 1 <= max_results <= 20:
        raise ValueError("max_results 必须在 1..20 之间")

    from ddgs import DDGS

    with DDGS() as ddgs:
        rows = list(ddgs.text(query, max_results=max_results))
    return [
        {
            "title": r.get("title") or "",
            "href": r.get("href") or "",
            "body": r.get("body") or "",
        }
        for r in rows
    ]
