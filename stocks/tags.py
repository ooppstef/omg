"""东财行业 / 概念标签服务：行业反查、热点概念名、概念归属。

rank 与 watch_advice 共用，收敛此前对 crowd / rank 私有函数的跨模块依赖。
抓取结果带会话级缓存（TTL），避免同一会话内 rank + watch_advice 重复全量拉取。

缓存契约：
- 返回给调用方的一律是副本（浅拷贝即可，元素为 str/tuple），禁止把缓存本体
  直接交出，防止调用方就地修改污染共享缓存（与 ai/tools.py 的 deepcopy 先例一致）。
- 测试请 patch `stocks.tags.*` 层；crowd/rank 的转发包装不再影响 tags 内部路径。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import pandas as pd
import requests

from stocks.util import env_int

HOT_CONCEPT_TOP = 20
MAX_CONCEPTS = 3
DEFAULT_CACHE_TTL_SECONDS = 300

_LOCK = threading.RLock()
_SESSION_LOCAL = threading.local()
_INDUSTRY_CACHE: Optional[tuple[float, dict]] = None
_CONSTITUENT_CACHE: dict[str, tuple[float, list]] = {}
_CONCEPT_MEMBERSHIP_CACHE: dict[str, tuple[float, set]] = {}
_CONCEPT_CATALOG_CACHE: Optional[tuple[float, pd.DataFrame]] = None
_HOT_NAMES_CACHE: Optional[tuple[float, list]] = None
_INDUSTRY_FETCH_LOCK = threading.Lock()
_CONCEPT_CATALOG_FETCH_LOCK = threading.Lock()
_HOT_NAMES_FETCH_LOCK = threading.Lock()
_CONSTITUENT_FETCH_LOCKS: dict[str, threading.Lock] = {}
_CONCEPT_FETCH_LOCKS: dict[str, threading.Lock] = {}


def _ttl() -> int:
    return env_int("OMG_TAGS_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)


def clear_tags_cache() -> None:
    """测试或强制刷新。"""
    global _CONCEPT_CATALOG_CACHE, _HOT_NAMES_CACHE, _INDUSTRY_CACHE
    with _LOCK:
        _INDUSTRY_CACHE = None
        _CONCEPT_CATALOG_CACHE = None
        _HOT_NAMES_CACHE = None
        _CONSTITUENT_CACHE.clear()
        _CONCEPT_MEMBERSHIP_CACHE.clear()


def _session() -> requests.Session:
    """每个工作线程独立 Session，避免并发修改共享 Session 状态。"""
    session = getattr(_SESSION_LOCAL, "session", None)
    if session is None:
        s = requests.Session()
        s.trust_env = False  # 避开坏掉的本地代理；东财 delay 主机可直连
        s.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            }
        )
        _SESSION_LOCAL.session = s
        session = s
    return session


def _lock_for(registry: dict[str, threading.Lock], key: str) -> threading.Lock:
    """同一个数据键只允许一个线程回源，不同键仍可并行。"""
    with _LOCK:
        lock = registry.get(key)
        if lock is None:
            lock = threading.Lock()
            registry[key] = lock
        return lock


def _normalize_em_diff(diff) -> list:
    """东财 diff 在不同节点可能返回 list 或数字键 dict。"""
    if not diff:
        return []
    if isinstance(diff, dict):
        return list(diff.values())
    if isinstance(diff, list):
        return diff
    return []


def _fetch_em_industries() -> dict:
    """东财全部行业板块 name -> BK 代码。"""
    out = {}
    fetched = 0
    valid = 0
    expected_total: Optional[int] = None
    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    for pn in range(1, 12):
        try:
            r = _session().get(
                url,
                params={
                    "pn": pn,
                    "pz": 100,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": "m:90+t:2",
                    "fields": "f12,f14",
                },
                timeout=15,
            )
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
        except Exception:
            return {}
        if expected_total is None:
            try:
                expected_total = int(data.get("total"))
            except (TypeError, ValueError):
                expected_total = None
        diff = _normalize_em_diff(data.get("diff"))
        if not diff:
            complete = expected_total is None or (
                fetched >= expected_total and valid >= expected_total
            )
            return out if complete else {}
        fetched += len(diff)
        for row in diff:
            if not isinstance(row, dict):
                continue
            name = str(row.get("f14") or "").strip()
            code = str(row.get("f12") or "").strip().upper()
            if name and code.startswith("BK"):
                out[name] = code
                valid += 1
        if expected_total is not None and fetched >= expected_total:
            return out if valid >= expected_total else {}
        if len(diff) < 100:
            return out if expected_total is None else {}
    return {}


def _fetch_em_constituents(bk_code: str) -> list:
    """东财行业成分 [(code, name), ...]。"""
    code = str(bk_code).strip().upper()
    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    rows = []
    fetched = 0
    valid = 0
    expected_total: Optional[int] = None
    for pn in range(1, 20):
        try:
            r = _session().get(
                url,
                params={
                    "pn": pn,
                    "pz": 100,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f3",
                    "fs": f"b:{code}",
                    "fields": "f12,f14",
                },
                timeout=12,
            )
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
        except Exception:
            return []
        if expected_total is None:
            try:
                expected_total = int(data.get("total"))
            except (TypeError, ValueError):
                expected_total = None
        diff = _normalize_em_diff(data.get("diff"))
        if not diff:
            complete = expected_total is None or (
                fetched >= expected_total and valid >= expected_total
            )
            return rows if complete else []
        fetched += len(diff)
        for item in diff:
            if not isinstance(item, dict):
                continue
            stock = str(item.get("f12") or "").strip()
            name = str(item.get("f14") or "").strip()
            if stock:
                rows.append((stock, name))
                valid += 1
        if expected_total is not None and fetched >= expected_total:
            return rows if valid >= expected_total else []
        if len(diff) < 100:
            return rows if expected_total is None else []
    return []


def _fetch_concept_catalog() -> pd.DataFrame:
    """同花顺概念目录；失败返回空表。"""
    try:
        import adata

        frame = adata.stock.info.all_concept_code_ths()
    except Exception:
        return pd.DataFrame()
    if (
        frame is None
        or frame.empty
        or not {"name", "index_code"}.issubset(frame.columns)
    ):
        return pd.DataFrame()
    return frame


def _concept_catalog_frame() -> pd.DataFrame:
    """同花顺概念目录（TTL 缓存，返回副本）。"""
    global _CONCEPT_CATALOG_CACHE
    ttl = _ttl()
    with _LOCK:
        cached = _CONCEPT_CATALOG_CACHE
        if cached is not None and time.monotonic() - cached[0] <= ttl:
            return cached[1].copy(deep=True)
    with _CONCEPT_CATALOG_FETCH_LOCK:
        with _LOCK:
            cached = _CONCEPT_CATALOG_CACHE
            if cached is not None and time.monotonic() - cached[0] <= ttl:
                return cached[1].copy(deep=True)
        frame = _fetch_concept_catalog()
        if frame.empty:
            return frame
        stored = frame.copy(deep=True)
        with _LOCK:
            _CONCEPT_CATALOG_CACHE = (time.monotonic(), stored)
        return stored.copy(deep=True)


def _concept_constituents_frame(index_code: str) -> pd.DataFrame:
    """同花顺概念成分股 DataFrame，带会话级缓存。"""
    key = str(index_code).strip()
    ttl = _ttl()
    with _LOCK:
        cached = _CONCEPT_MEMBERSHIP_CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] <= ttl:
            return pd.DataFrame({"stock_code": sorted(cached[1])})
    with _lock_for(_CONCEPT_FETCH_LOCKS, key):
        with _LOCK:
            cached = _CONCEPT_MEMBERSHIP_CACHE.get(key)
            if cached is not None and time.monotonic() - cached[0] <= ttl:
                return pd.DataFrame({"stock_code": sorted(cached[1])})
        try:
            import adata

            df = adata.stock.info.concept_constituent_ths(index_code=key)
        except Exception:
            return pd.DataFrame()
        if df is None or df.empty or "stock_code" not in df.columns:
            return pd.DataFrame()
        codes = {str(c).strip() for c in df["stock_code"].tolist() if str(c).strip()}
        if not codes:
            return pd.DataFrame()
        with _LOCK:
            _CONCEPT_MEMBERSHIP_CACHE[key] = (time.monotonic(), codes)
        return pd.DataFrame({"stock_code": sorted(codes)})


def em_industries() -> dict[str, str]:
    """东财全部行业板块 name -> BK 代码（会话级缓存）。"""
    global _INDUSTRY_CACHE
    ttl = _ttl()
    with _LOCK:
        cached = _INDUSTRY_CACHE
        if cached is not None and time.monotonic() - cached[0] <= ttl:
            return dict(cached[1])
    with _INDUSTRY_FETCH_LOCK:
        with _LOCK:
            cached = _INDUSTRY_CACHE
            if cached is not None and time.monotonic() - cached[0] <= ttl:
                return dict(cached[1])
        out = _fetch_em_industries()
        if out:
            with _LOCK:
                _INDUSTRY_CACHE = (time.monotonic(), dict(out))
        return dict(out)


def em_constituents(bk_code: str) -> list[tuple[str, str]]:
    """东财行业成分 [(code, name), ...]（会话级缓存）。"""
    key = str(bk_code).strip().upper()
    ttl = _ttl()
    with _LOCK:
        cached = _CONSTITUENT_CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] <= ttl:
            return list(cached[1])
    with _lock_for(_CONSTITUENT_FETCH_LOCKS, key):
        with _LOCK:
            cached = _CONSTITUENT_CACHE.get(key)
            if cached is not None and time.monotonic() - cached[0] <= ttl:
                return list(cached[1])
        rows = _fetch_em_constituents(key)
        if rows:
            with _LOCK:
                _CONSTITUENT_CACHE[key] = (time.monotonic(), list(rows))
        return list(rows)


def industry_by_code(needed: set[str]) -> dict[str, Optional[str]]:
    """股票代码 -> 行业名（多行业取字典序最小；无匹配为 None）。"""
    if not needed:
        return {}
    em = em_industries()
    if not em:
        return {code: None for code in needed}

    def one(item: tuple[str, str]) -> dict[str, set[str]]:
        name, bk = item
        local: dict[str, set[str]] = {}
        for code, _stock_name in em_constituents(bk):
            code = str(code).strip()
            if code in needed:
                local.setdefault(code, set()).add(name)
        return local

    merged: dict[str, set[str]] = {code: set() for code in needed}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for partial in pool.map(one, list(em.items())):
            for code, names in partial.items():
                merged[code].update(names)

    return {
        code: (sorted(names)[0] if names else None)
        for code, names in merged.items()
    }


def _fetch_hot_concept_names(top: int) -> list[str]:
    """当前热点概念名：优先同花顺热概念（轻量），强度榜作回退。"""
    from stocks import ana

    try:
        concepts = ana.hot_concept_ths(top=top, print_result=False) or []
        names = [
            str(getattr(c, "concept_name", None) or getattr(c, "name", "") or "")
            for c in concepts
        ]
        names = [n for n in names if n]
        if names:
            return names[:top]
    except Exception:
        pass
    daily = ana.concept_strength_top10(days=5, top=top, print_result=False)
    if isinstance(daily, dict) and daily:
        latest = max(daily.keys())
        return [str(name) for name, _score in daily[latest][:top]]
    return []


def hot_concept_names(top: int = HOT_CONCEPT_TOP) -> list[str]:
    """当前热点概念名（会话级缓存）。

    缓存统一存 HOT_CONCEPT_TOP 全量，返回时再按调用方 top 切片，
    避免缓存内容被首次调用的 top 截断。
    """
    global _HOT_NAMES_CACHE
    ttl = _ttl()
    with _LOCK:
        cached = _HOT_NAMES_CACHE
        if cached is not None and time.monotonic() - cached[0] <= ttl:
            return cached[1][:top]
    with _HOT_NAMES_FETCH_LOCK:
        with _LOCK:
            cached = _HOT_NAMES_CACHE
            if cached is not None and time.monotonic() - cached[0] <= ttl:
                return cached[1][:top]
        names = _fetch_hot_concept_names(HOT_CONCEPT_TOP)
        if names:
            with _LOCK:
                _HOT_NAMES_CACHE = (time.monotonic(), list(names))
        return list(names[:top])


def concepts_by_code(
    codes: set[str],
    hot_names: list[str],
    *,
    concept_df: Optional[pd.DataFrame] = None,
    fetch_constituent: Optional[Callable[[str], pd.DataFrame]] = None,
) -> dict[str, list[str]]:
    """股票代码 -> 命中热点概念名（按热榜顺序，最多 MAX_CONCEPTS 个）。"""
    if not hot_names or not codes:
        return {code: [] for code in codes}

    if concept_df is None:
        concept_df = _concept_catalog_frame()
    if fetch_constituent is None:
        fetch_constituent = _concept_constituents_frame

    if concept_df is None or concept_df.empty:
        return {code: [] for code in codes}

    name_to_code: dict[str, str] = {}
    for _, row in concept_df.iterrows():
        name = str(row.get("name") or "").strip()
        code = str(row.get("index_code") or "").strip()
        if name and code and name not in name_to_code:
            name_to_code[name] = code

    ordered = [(n, name_to_code[n]) for n in hot_names if n in name_to_code]
    membership: dict[str, list[str]] = {code: [] for code in codes}

    def fetch(pair: tuple[str, str]) -> tuple[str, set[str]]:
        name, index_code = pair
        try:
            df = fetch_constituent(index_code)
        except Exception:
            return name, set()
        if df is None or df.empty or "stock_code" not in df.columns:
            return name, set()
        member_codes = {str(c).strip() for c in df["stock_code"].tolist()}
        return name, member_codes & codes

    results: dict[str, set[str]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fmap = {pool.submit(fetch, p): p[0] for p in ordered}
        for fut in as_completed(fmap):
            name, member_codes = fut.result()
            results[name] = member_codes

    for name in hot_names:
        for code in results.get(name) or set():
            if len(membership[code]) < MAX_CONCEPTS:
                membership[code].append(name)
    return membership
