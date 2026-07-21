"""个股涨跌幅榜：涨幅/跌幅 Top，供 omg chat tool `rank` 取证。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from stocks.universe import A_SHARE_CODE_PREFIXES
from stocks.util import finite_float

DEFAULT_GAINERS = 50
DEFAULT_LOSERS = 20
MAX_GAINERS = 100
MAX_LOSERS = 50
HOT_CONCEPT_TOP = 20
MAX_CONCEPTS = 3
UNIVERSE = "hs_main_cyb_kcb_ex_st"
CODE_PREFIXES = A_SHARE_CODE_PREFIXES
_TZ = ZoneInfo("Asia/Shanghai")


def _validate_n(name: str, value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须在 1..{maximum} 之间")
    return value


def _is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


def _in_universe(code: str) -> bool:
    c = (code or "").strip()
    return c.startswith(CODE_PREFIXES)


def _first_col(frame: pd.DataFrame, names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        if name in frame.columns:
            return name
    return None


def filter_universe(df: pd.DataFrame) -> pd.DataFrame:
    """过滤到默认宇宙；保留数值列已解析的行。"""
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.copy()
    col = {
        "code": _first_col(frame, ("代码", "code", "stock_code")),
        "name": _first_col(frame, ("名称", "name", "short_name")),
        "change_pct": _first_col(frame, ("涨跌幅", "change_pct", "change_percent")),
        "price": _first_col(frame, ("最新价", "price")),
        "amount": _first_col(frame, ("成交额", "amount")),
        "turnover": _first_col(frame, ("换手率", "turnover")),
    }
    if not col["code"] or not col["change_pct"]:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "code": frame[col["code"]].astype(str).str.strip(),
            "name": (
                frame[col["name"]].astype(str).str.strip() if col["name"] else ""
            ),
            "change_pct": pd.to_numeric(frame[col["change_pct"]], errors="coerce"),
            "price": (
                pd.to_numeric(frame[col["price"]], errors="coerce")
                if col["price"]
                else float("nan")
            ),
            "amount": (
                pd.to_numeric(frame[col["amount"]], errors="coerce")
                if col["amount"]
                else float("nan")
            ),
            "turnover": (
                pd.to_numeric(frame[col["turnover"]], errors="coerce")
                if col["turnover"]
                else float("nan")
            ),
        }
    )
    mask = out["code"].map(_in_universe)
    mask &= ~out["name"].map(_is_st)
    chg_ok = out["change_pct"].map(lambda x: finite_float(x) is not None)
    price_ok = out["price"].map(
        lambda x: (finite_float(x) is not None) and float(x) > 0
    )
    mask &= chg_ok & price_ok
    return out.loc[mask].reset_index(drop=True)


def _fetch_spot() -> pd.DataFrame:
    import akshare as ak

    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for label, fn in (
        ("sh", ak.stock_sh_a_spot_em),
        ("sz", ak.stock_sz_a_spot_em),
    ):
        try:
            part = fn()
            if part is not None and not part.empty:
                frames.append(part)
            else:
                errors.append(f"{label}:empty")
        except Exception as exc:
            errors.append(f"{label}:{exc}")
    if len(frames) == 2:
        return pd.concat(frames, ignore_index=True)
    # 单侧成功不静默半市场：先兜底全市场，再失败
    try:
        whole = ak.stock_zh_a_spot_em()
    except Exception as exc:
        errors.append(f"all:{exc}")
        whole = None
    if whole is not None and not whole.empty:
        return whole
    raise ValueError(
        f"拉取现货失败（分市场完整度 {len(frames)}/2）: {errors}"
    )


def _row_payload(
    row: pd.Series,
    *,
    industry: Optional[str],
    concepts: list[str],
) -> dict[str, Any]:
    return {
        "code": str(row["code"]),
        "name": str(row["name"]),
        "change_pct": float(row["change_pct"]),
        "price": finite_float(row["price"]),
        "amount": finite_float(row["amount"]),
        "turnover": finite_float(row["turnover"]),
        "industry": industry,
        "concepts": list(concepts),
    }


def _attach_tags(
    rows: list[dict[str, Any]],
    industry_by_code: dict[str, Optional[str]],
    concepts_by_code: dict[str, list[str]],
) -> int:
    """原地挂标签，返回 tag_miss 计数。"""
    miss = 0
    for item in rows:
        code = item["code"]
        industry = industry_by_code.get(code)
        concepts = list(concepts_by_code.get(code) or [])[:MAX_CONCEPTS]
        item["industry"] = industry
        item["concepts"] = concepts
        if industry is None and not concepts:
            miss += 1
    return miss


def _live_industry_by_code(needed: set[str]) -> dict[str, Optional[str]]:
    """东财行业成分反查；多行业取字典序最小。"""
    if not needed:
        return {}
    from stocks import crowd

    em = crowd._list_em_industries()
    if not em:
        return {code: None for code in needed}

    def one(item: tuple[str, str]) -> dict[str, set[str]]:
        name, bk = item
        local: dict[str, set[str]] = {}
        for code, _stock_name in crowd._fetch_em_constituents(bk):
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


def _live_hot_concept_names(top: int = HOT_CONCEPT_TOP) -> list[str]:
    from stocks import ana

    # 优先同花顺热概念（轻量）；强度 Top 作回退，避免每次 rank 全量重算 hot
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


def _live_concepts_by_code(
    hot_names: list[str],
    needed: set[str],
    *,
    concept_df: Optional[pd.DataFrame] = None,
    fetch_constituent=None,
) -> dict[str, list[str]]:
    if not hot_names or not needed:
        return {code: [] for code in needed}

    if concept_df is None or fetch_constituent is None:
        import adata

        if concept_df is None:
            try:
                concept_df = adata.stock.info.all_concept_code_ths()
            except Exception:
                return {code: [] for code in needed}
        if fetch_constituent is None:
            fetch_constituent = (
                lambda index_code: adata.stock.info.concept_constituent_ths(
                    index_code=index_code
                )
            )

    if concept_df is None or concept_df.empty:
        return {code: [] for code in needed}

    name_to_code: dict[str, str] = {}
    for _, row in concept_df.iterrows():
        name = str(row.get("name") or "").strip()
        code = str(row.get("index_code") or "").strip()
        if name and code and name not in name_to_code:
            name_to_code[name] = code

    ordered = [(n, name_to_code[n]) for n in hot_names if n in name_to_code]
    membership: dict[str, list[str]] = {code: [] for code in needed}

    def fetch(pair: tuple[str, str]) -> tuple[str, set[str]]:
        name, index_code = pair
        try:
            df = fetch_constituent(index_code)
        except Exception:
            return name, set()
        if df is None or df.empty or "stock_code" not in df.columns:
            return name, set()
        codes = {str(c).strip() for c in df["stock_code"].tolist()}
        return name, codes & needed

    results: dict[str, set[str]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fmap = {pool.submit(fetch, p): p[0] for p in ordered}
        for fut in as_completed(fmap):
            name, codes = fut.result()
            results[name] = codes

    for name in hot_names:
        codes = results.get(name) or set()
        for code in codes:
            if len(membership[code]) < MAX_CONCEPTS:
                membership[code].append(name)
    return membership


def analyze(
    gainers: int = DEFAULT_GAINERS,
    losers: int = DEFAULT_LOSERS,
    *,
    spot_df: Optional[pd.DataFrame] = None,
    industry_by_code: Optional[dict[str, Optional[str]]] = None,
    concepts_by_code: Optional[dict[str, list[str]]] = None,
    skip_tags: bool = False,
) -> dict[str, Any]:
    """
    返回涨跌幅榜结构化结果。
    filtered==0 或两侧皆空时 raise ValueError。
    """
    gainers_n = _validate_n("gainers", gainers, MAX_GAINERS)
    losers_n = _validate_n("losers", losers, MAX_LOSERS)

    raw = spot_df if spot_df is not None else _fetch_spot()
    scanned = 0 if raw is None else int(len(raw))
    filtered_df = filter_universe(raw)
    filtered = int(len(filtered_df))
    if filtered == 0:
        raise ValueError("无可排序标的（宇宙过滤后为空）")

    top = filtered_df.sort_values(
        by=["change_pct", "code"], ascending=[False, True], kind="mergesort"
    ).head(gainers_n)
    bottom = filtered_df.sort_values(
        by=["change_pct", "code"], ascending=[True, True], kind="mergesort"
    ).head(losers_n)

    gainer_rows = [
        _row_payload(row, industry=None, concepts=[]) for _, row in top.iterrows()
    ]
    loser_rows = [
        _row_payload(row, industry=None, concepts=[]) for _, row in bottom.iterrows()
    ]
    if not gainer_rows and not loser_rows:
        raise ValueError("涨跌幅榜两侧皆空")

    needed = {r["code"] for r in gainer_rows} | {r["code"] for r in loser_rows}
    if skip_tags:
        tag_miss = len(gainer_rows) + len(loser_rows)
    else:
        try:
            ind = (
                industry_by_code
                if industry_by_code is not None
                else _live_industry_by_code(needed)
            )
        except Exception:
            ind = {code: None for code in needed}
        try:
            if concepts_by_code is not None:
                cons = concepts_by_code
            else:
                hot = _live_hot_concept_names()
                cons = _live_concepts_by_code(hot, needed)
        except Exception:
            cons = {code: [] for code in needed}
        tag_miss = _attach_tags(gainer_rows, ind, cons)
        tag_miss += _attach_tags(loser_rows, ind, cons)

    as_of = datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "as_of": as_of,
        "universe": UNIVERSE,
        "gainers": gainer_rows,
        "losers": loser_rows,
        "meta": {
            "gainers_n": gainers_n,
            "losers_n": losers_n,
            "scanned": scanned,
            "filtered": filtered,
            "tag_miss": tag_miss,
        },
    }
