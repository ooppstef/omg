"""自选股操作建议取证：技术指标 × 板块 × 板走势 × 大盘锚。"""

from __future__ import annotations

import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from stocks.util import finite_float, recent_date

MAX_WATCH = 30
# 足够覆盖 MA60 / MACD 预热
_KLINE_LOOKBACK_DAYS = 180
_TZ = ZoneInfo("Asia/Shanghai")

_TECH_FIELDS = (
    "close",
    "change_pct",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "dif",
    "dea",
    "macd_hist",
    "adx",
    "plus_di",
    "minus_di",
    "ppo",
    "ppo_hist",
    "mfi",
    "atr",
    "natr",
    "rel_vol",
    "rv20",
    "close_loc",
    "amplitude",
)


def _as_of_date(value) -> Optional[str]:
    if value is None:
        return None
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _source_dates(
    items: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    market: dict[str, Any],
) -> list[str]:
    values = [
        *(item.get("quote_as_of") for item in items),
        *(
            (item.get("tech") or {}).get("as_of")
            for item in items
            if isinstance(item.get("tech"), dict)
        ),
        *(row.get("as_of") for row in indexes),
        (market.get("mood") or {}).get("trade_date"),
        (market.get("style") or {}).get("as_of"),
        market.get("hot_as_of"),
    ]
    for item in items:
        board = item.get("board")
        if not isinstance(board, dict):
            continue
        for section, date_key in (
            ("rot", "as_of"),
            ("crowd", "trade_date"),
            ("etf", "as_of"),
        ):
            evidence = board.get(section)
            if isinstance(evidence, dict):
                values.append(evidence.get(date_key))
    return sorted(
        {
            as_of
            for value in values
            if (as_of := _as_of_date(value)) is not None
        }
    )


def _vol_stance(rel_vol: float | None) -> str | None:
    """把 rel_vol 落成可读量能标签，供复盘直接引用。"""
    rel_vol = finite_float(rel_vol)
    if rel_vol is None:
        return None
    if rel_vol >= 1.5:
        return "明显放量"
    if rel_vol >= 1.2:
        return "量能放大"
    if rel_vol >= 0.85:
        return "量能大致持平"
    return "量能偏弱"


def _quote_df(rows: list[tuple]) -> pd.DataFrame:
    """filter.db._fetch_one 的 rows → OHLCV DataFrame。"""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=[
            "code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "change_pct",
            "turnover",
        ],
    )
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def _tech_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    from stocks.filter.indicator import compute_all

    if df is None or df.empty or len(df) < 30:
        raise ValueError("K 线不足，无法计算指标")
    out = compute_all(df)
    last = out.iloc[-1]
    snap: dict[str, Any] = {
        "as_of": str(last.get("trade_date") or "")[:10],
    }
    for key in _TECH_FIELDS:
        if key not in out.columns:
            snap[key] = None
            continue
        snap[key] = finite_float(last.get(key))
    close = snap.get("close")
    for ma_key in ("ma5", "ma10", "ma20", "ma60"):
        ma = snap.get(ma_key)
        if close is not None and ma is not None and ma != 0:
            snap[f"vs_{ma_key}_pct"] = round((close / ma - 1.0) * 100, 2)
        else:
            snap[f"vs_{ma_key}_pct"] = None
    # 原始量/额便于对照；vol_stance 给模型少算一步
    snap["volume"] = finite_float(last.get("volume"))
    snap["amount"] = finite_float(last.get("amount"))
    snap["vol_stance"] = _vol_stance(snap.get("rel_vol"))
    return snap


def _sina_market_prefix(code: str) -> str:
    """A 股代码 -> 新浪日线市场前缀（sh/sz）。"""
    c = str(code).strip()
    if c.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _df_from_ak_daily(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    # 统一列名
    rename = {"date": "trade_date"}
    out = out.rename(columns=rename)
    if "trade_date" not in out.columns:
        return pd.DataFrame()
    out["code"] = code
    if "amount" not in out.columns:
        out["amount"] = None
    if "turnover" not in out.columns:
        out["turnover"] = None
    if "change_pct" not in out.columns:
        out["change_pct"] = None

    required = ("open", "high", "low", "close", "volume")
    if not set(required).issubset(out.columns):
        return pd.DataFrame()
    parsed_dates = pd.to_datetime(out["trade_date"], errors="coerce")
    for column in (*required, "amount", "change_pct", "turnover"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.replace([float("inf"), float("-inf")], pd.NA)

    valid = parsed_dates.notna()
    valid &= (out[list(required[:4])] > 0).all(axis=1)
    valid &= out["volume"] > 0
    valid &= out["high"] >= out[["open", "close", "low"]].max(axis=1)
    valid &= out["low"] <= out[["open", "close", "high"]].min(axis=1)
    out = out.loc[valid].copy()
    if out.empty:
        return pd.DataFrame()

    out["trade_date"] = parsed_dates.loc[valid].dt.strftime("%Y-%m-%d")
    out.loc[out["amount"] < 0, "amount"] = pd.NA
    out.loc[out["turnover"] < 0, "turnover"] = pd.NA
    out = out.drop_duplicates(subset=["trade_date"], keep="last")
    out = out.sort_values("trade_date").reset_index(drop=True)
    computed_change = out["close"].pct_change(fill_method=None) * 100
    out["change_pct"] = out["change_pct"].fillna(computed_change)

    cols = [
        "code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "change_pct",
        "turnover",
    ]
    return out[cols]


def _fetch_kline_akshare(code: str, *, start: str) -> pd.DataFrame:
    """新浪日线（akshare）；东财 push2his 在代理下常空返回。"""
    import akshare as ak

    from stocks.util import force_direct_http

    symbol = f"{_sina_market_prefix(code)}{code}"
    start_compact = start.replace("-", "")
    end_compact = recent_date(0).replace("-", "")
    with force_direct_http():
        df = ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=start_compact,
            end_date=end_compact,
            adjust="qfq",
        )
    return _df_from_ak_daily(df, code)


def _fetch_kline_local(code: str, *, start: str) -> pd.DataFrame:
    """本地 filter DailyQuote 回退（可能滞后）。"""
    from stocks.filter import db as filter_db

    try:
        with filter_db._conn() as conn:
            rows = conn.execute(
                "SELECT code, trade_date, open, high, low, close, volume, "
                "amount, change_pct, turnover FROM DailyQuote "
                "WHERE code=? AND trade_date>=? ORDER BY trade_date",
                (code, start),
            ).fetchall()
    except Exception:
        return pd.DataFrame()
    return filter_db._sanitize_quote_frame(_quote_df(list(rows)))


_MIN_BARS = 30


def _fetch_tech_for_code(code: str, *, start: str) -> tuple[Optional[dict], Optional[str]]:
    """拉取日线并算技术快照。优先新浪，其次本地库；adata/东财 his 作末选。"""
    errors: list[str] = []
    # (source, frame) 按优先级尝试；不足 _MIN_BARS 的源会继续往后试
    ordered: list[tuple[str, pd.DataFrame]] = []

    try:
        ak_df = _fetch_kline_akshare(code, start=start)
        if ak_df is None or ak_df.empty:
            errors.append("akshare:empty")
        else:
            ordered.append(("akshare", ak_df))
            if len(ak_df) < _MIN_BARS:
                errors.append(f"akshare:short:{len(ak_df)}")
    except Exception as exc:
        errors.append(f"akshare:{type(exc).__name__}")

    need_more = not ordered or len(ordered[0][1]) < _MIN_BARS
    if need_more:
        local = _fetch_kline_local(code, start=start)
        if local is None or local.empty:
            errors.append("local:empty")
        else:
            ordered.append(("local_db", local))
            if len(local) < _MIN_BARS:
                errors.append(f"local:short:{len(local)}")

    need_more = not any(len(df) >= _MIN_BARS for _, df in ordered)
    if need_more:
        try:
            from stocks.filter import db as filter_db

            _code, rows, err = filter_db._fetch_one(code, start)
            if err is not None:
                errors.append(f"adata:{type(err).__name__}")
            elif not rows:
                errors.append("adata:empty")
            else:
                ordered.append(("adata", _quote_df(rows)))
        except Exception as exc:
            errors.append(f"adata:{type(exc).__name__}")

    # 先够长的，再按源优先级；同档比长度
    pri = {"akshare": 0, "local_db": 1, "adata": 2}

    def _rank(item: tuple[str, pd.DataFrame]) -> tuple:
        src, frame = item
        n = len(frame)
        long_enough = 0 if n >= _MIN_BARS else 1
        return (long_enough, pri.get(src, 9), -n)

    ordered.sort(key=_rank)
    last_tech_err: Optional[str] = None
    for src, frame in ordered:
        if frame is None or frame.empty:
            continue
        try:
            snap = _tech_snapshot(frame)
            snap["source"] = src
            if src == "local_db":
                snap["stale_warning"] = True
            return snap, None
        except Exception as exc:
            last_tech_err = f"tech:{type(exc).__name__}: {exc}"
            errors.append(f"{src}:{last_tech_err}")

    detail = ",".join(errors) if errors else "unknown"
    if last_tech_err:
        return None, f"{last_tech_err}:{detail}"
    return None, f"kline:empty:{detail}"


def _build_hot_score_map(hot_data) -> dict[str, float]:
    """concept_strength_top10 最新日 name -> score。"""
    if not isinstance(hot_data, dict) or not hot_data:
        return {}
    latest = max(hot_data.keys())
    out: dict[str, float] = {}
    for name, score in hot_data.get(latest) or []:
        value = finite_float(score)
        if value is not None:
            out[str(name)] = value
    return out


def _index_rot(rot_data: Optional[dict]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(rot_data, dict):
        return indexed
    for group, rows in rot_data.items():
        for row in rows or []:
            name = str(row.get("name") or "").strip()
            if not name or name in indexed:
                continue
            indexed[name] = {
                "group": group,
                "score": finite_float(row.get("score")),
                "reason": row.get("reason"),
                "as_of": row.get("as_of"),
            }
    return indexed


def _index_crowd(crowd_rows: Optional[list]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in crowd_rows or []:
        name = str(
            row.get("board_name")
            or row.get("concept_name")
            or row.get("name")
            or ""
        ).strip()
        if not name:
            continue
        indexed[name] = {
            "crowd_pct": finite_float(row.get("crowd_pct")),
            "change_pct": finite_float(row.get("change_pct")),
            "trade_date": row.get("trade_date"),
        }
    return indexed


def _match_etf(
    industry: Optional[str],
    concepts: list[str],
    etf_rows: Optional[list],
) -> Optional[dict[str, Any]]:
    needles = [n for n in ([industry] if industry else []) + list(concepts) if n]
    if not needles or not etf_rows:
        return None
    best = None
    for row in etf_rows:
        theme = str(row.get("theme") or "")
        name = str(row.get("name") or "")
        blob = theme + name
        hit = next((n for n in needles if n and n in blob), None)
        if not hit and theme:
            hit = next((n for n in needles if theme and theme in n), None)
        if not hit:
            continue
        cand = {
            "theme": theme,
            "code": row.get("code"),
            "name": name,
            "short_state": row.get("short_state"),
            "mid_state": row.get("mid_state"),
            "change_pct": finite_float(row.get("change_pct")),
            "as_of": row.get("as_of"),
            "target_as_of": row.get("target_as_of"),
            "volume_status": row.get("volume_status"),
            "matched_by": hit,
        }
        if best is None:
            best = cand
    return best


def _board_for_item(
    *,
    industry: Optional[str],
    concepts: list[str],
    rot_index: dict[str, dict[str, Any]],
    hot_scores: dict[str, float],
    crowd_index: dict[str, dict[str, Any]],
    etf_rows: Optional[list],
) -> dict[str, Any]:
    board: dict[str, Any] = {
        "rot": None,
        "hot": [],
        "crowd": None,
        "etf": None,
    }
    names = list(concepts)
    if industry and industry not in names:
        names = [industry] + names
    for name in names:
        if name in rot_index and board["rot"] is None:
            board["rot"] = {"name": name, **rot_index[name]}
        if name in hot_scores:
            board["hot"].append({"name": name, "score": hot_scores[name]})
    board["hot"] = sorted(
        board["hot"], key=lambda x: x.get("score") or 0.0, reverse=True
    )[:5]
    if industry and industry in crowd_index:
        board["crowd"] = {"name": industry, **crowd_index[industry]}
    board["etf"] = _match_etf(industry, concepts, etf_rows)
    return board


def analyze(
    *,
    skip_board: bool = True,
    max_n: int = MAX_WATCH,
    watch_rows: Optional[list] = None,
    index_rows: Optional[list] = None,
    industry_by_code: Optional[dict[str, Optional[str]]] = None,
    concepts_by_code: Optional[dict[str, list[str]]] = None,
    tech_by_code: Optional[dict[str, dict[str, Any]]] = None,
    market: Optional[dict[str, Any]] = None,
    rot_data: Optional[dict] = None,
    hot_data: Optional[dict] = None,
    crowd_rows: Optional[list] = None,
    etf_rows: Optional[list] = None,
) -> dict[str, Any]:
    """组装自选逐票取证。可注入依赖便于单测。"""
    import stocks.market as market_mod
    import stocks.watch_list as watch_list
    from stocks import rank

    errors: list[str] = []
    truncated = False

    if watch_rows is None:
        try:
            watch_rows = watch_list.get_watch_list_current_price(print_result=False) or []
        except Exception as exc:
            watch_rows = []
            errors.append(f"watchlist:{type(exc).__name__}: {exc}")

    if index_rows is None:
        indexes = [
            ("000001", "上证指数"),
            ("399001", "深证成指"),
            ("399006", "创业板指"),
        ]

        def fetch_index(item):
            try:
                row = market_mod.get_index_current_price(item[0], item[1])
                if row is None:
                    errors.append(f"index:{item[1]}:empty")
                return row
            except Exception as exc:
                errors.append(f"index:{item[1]}:{type(exc).__name__}")
                return None

        with ThreadPoolExecutor(max_workers=3) as ex:
            index_rows = [r for r in ex.map(fetch_index, indexes) if r is not None]

    rows = list(watch_rows or [])
    if len(rows) > max_n:
        truncated = True
        errors.append(f"watchlist:truncated:{len(rows)}->{max_n}")
        rows = rows[:max_n]

    if not rows:
        errors.append("watchlist:empty")

    codes = []
    for row in rows:
        code = str(getattr(row, "code", None) or (row.get("code") if isinstance(row, dict) else "") or "").strip()
        if code:
            codes.append(code)
    needed = set(codes)

    # —— 技术面 ——
    tech_map: dict[str, dict[str, Any]] = dict(tech_by_code or {})
    tech_errors: dict[str, str] = {}
    missing_tech = [c for c in codes if c not in tech_map]
    if missing_tech:
        start = recent_date(_KLINE_LOOKBACK_DAYS)
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(missing_tech)))) as pool:
            futs = {
                pool.submit(_fetch_tech_for_code, code, start=start): code
                for code in missing_tech
            }
            for fut in as_completed(futs):
                code = futs[fut]
                snap, err = fut.result()
                if snap is not None:
                    tech_map[code] = snap
                if err:
                    tech_errors[code] = err

    # —— 标签（热点名单可与 board.hot 共用一次拉取）——
    do_board = (not skip_board) and bool(rows)
    shared_hot = hot_data
    if industry_by_code is None:
        try:
            industry_by_code = rank._live_industry_by_code(needed) if needed else {}
        except Exception as exc:
            industry_by_code = {c: None for c in needed}
            errors.append(f"industry:{type(exc).__name__}")
    need_hot = (concepts_by_code is None and bool(needed)) or (
        do_board and shared_hot is None
    )
    if need_hot and shared_hot is None:
        try:
            import stocks.ana as ana

            shared_hot = ana.concept_strength_top10(
                days=10, top=15, print_result=False
            )
        except Exception as exc:
            shared_hot = None
            errors.append(f"hot:{type(exc).__name__}")
    if concepts_by_code is None:
        try:
            hot_names = []
            if isinstance(shared_hot, dict) and shared_hot:
                latest = max(shared_hot.keys())
                hot_names = [
                    str(name) for name, _score in (shared_hot.get(latest) or [])
                ]
            if not hot_names and needed:
                hot_names = rank._live_hot_concept_names()
            concepts_by_code = (
                rank._live_concepts_by_code(hot_names, needed) if needed else {}
            )
        except Exception as exc:
            concepts_by_code = {c: [] for c in needed}
            errors.append(f"concepts:{type(exc).__name__}")
    hot_data = shared_hot

    # —— 大盘 / 板走势 ——
    market_payload = dict(market or {})
    rot_index: dict[str, dict[str, Any]] = {}
    hot_scores: dict[str, float] = {}
    crowd_index: dict[str, dict[str, Any]] = {}
    etf_list = list(etf_rows) if etf_rows is not None else None

    # 无自选时不做板面对齐取证（避免空转）
    if do_board:
        if market is None:
            try:
                import stocks.mood as mood

                history = mood.fetch_mood_history(days=3)
                if history:
                    latest = history[-1]
                    market_payload["mood"] = {
                        "trade_date": latest.get("trade_date"),
                        "state": latest.get("state"),
                        "metrics": {
                            k: latest.get("metrics", {}).get(k)
                            for k in (
                                "limit_up",
                                "limit_down",
                                "zhaban_rate",
                                "height",
                            )
                        },
                    }
                else:
                    errors.append("mood:empty")
            except Exception as exc:
                errors.append(f"mood:{type(exc).__name__}")
            try:
                import stocks.style as style

                style_res = style.analyze(days=5)
                market_payload["style"] = {
                    "as_of": style_res.get("as_of"),
                    "state": style_res.get("state"),
                    "reasons": list(style_res.get("reasons") or [])[:3],
                }
            except Exception as exc:
                errors.append(f"style:{type(exc).__name__}")

        if rot_data is None:
            try:
                import stocks.rot as rot

                rot_data = rot.analyze(days=5, top_concepts=10, top_stocks=3)
            except Exception as exc:
                rot_data = None
                errors.append(f"rot:{type(exc).__name__}")
        if not rot_data or not any(rot_data.values() if isinstance(rot_data, dict) else []):
            if "rot:empty" not in errors and not any(
                e.startswith("rot:") for e in errors
            ):
                errors.append("rot:empty")
        rot_index = _index_rot(rot_data)

        if hot_data is None:
            try:
                import stocks.ana as ana

                hot_data = ana.concept_strength_top10(
                    days=10, top=15, print_result=False
                )
            except Exception as exc:
                hot_data = None
                errors.append(f"hot:{type(exc).__name__}")
        hot_scores = _build_hot_score_map(hot_data)
        if isinstance(hot_data, dict) and hot_data:
            market_payload["hot_as_of"] = max(hot_data)
        if not hot_scores and not any(e.startswith("hot:") for e in errors):
            errors.append("hot:empty")

        if crowd_rows is None:
            try:
                import stocks.crowd as crowd

                # 全量 available 行业（勿用报告 Top 切片，否则自选行业常假缺口）
                crowd_rows = crowd.scan_concept_crowding(top=10_000, top_stocks=0)
            except Exception as exc:
                crowd_rows = None
                errors.append(f"crowd:{type(exc).__name__}")
        crowd_index = _index_crowd(crowd_rows)
        if not crowd_index and not any(e.startswith("crowd:") for e in errors):
            errors.append("crowd:empty")

        if etf_list is None:
            try:
                import stocks.etf as etf

                # 全量 available ETF，不做 select_for_report 截断
                etf_list = etf.scan_etf_states(for_report=False)
            except Exception as exc:
                etf_list = None
                errors.append(f"etf:{type(exc).__name__}")
        if not etf_list and not any(e.startswith("etf:") for e in errors):
            errors.append("etf:empty")

    items: list[dict[str, Any]] = []
    for row in rows:
        if hasattr(row, "code"):
            code = str(row.code).strip()
            name = str(getattr(row, "name", "") or "")
            price = finite_float(getattr(row, "price", None))
            change_pct = finite_float(getattr(row, "change_percent", None))
            quote_as_of = getattr(row, "as_of", None)
        else:
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "")
            price = finite_float(row.get("price"))
            change_pct = finite_float(
                row.get("change_percent", row.get("change_pct"))
            )
            quote_as_of = row.get("as_of", row.get("trade_date"))
        item_errors: list[str] = []
        if code in tech_errors:
            item_errors.append(tech_errors[code])
        industry = (industry_by_code or {}).get(code)
        concepts = list((concepts_by_code or {}).get(code) or [])
        tech = tech_map.get(code)
        if tech is None and code not in tech_errors:
            item_errors.append("tech:missing")
        board = None
        if do_board:
            board = _board_for_item(
                industry=industry,
                concepts=concepts,
                rot_index=rot_index,
                hot_scores=hot_scores,
                crowd_index=crowd_index,
                etf_rows=etf_list,
            )
        items.append(
            {
                "code": code,
                "name": name,
                "price": price,
                "change_pct": change_pct,
                "quote_as_of": _as_of_date(quote_as_of),
                "industry": industry,
                "concepts": concepts,
                "tech": tech,
                "board": board,
                "errors": item_errors,
            }
        )

    indexes_payload = [
        {
            "code": getattr(r, "code", None)
            or (r.get("code") if isinstance(r, dict) else None),
            "name": getattr(r, "name", None)
            or (r.get("name") if isinstance(r, dict) else None),
            "price": finite_float(
                getattr(r, "price", None)
                if not isinstance(r, dict)
                else r.get("price")
            ),
            "change_percent": finite_float(
                getattr(r, "change_percent", None)
                if not isinstance(r, dict)
                else r.get("change_percent", r.get("change_pct"))
            ),
            "amount": finite_float(
                getattr(r, "amount", None)
                if not isinstance(r, dict)
                else r.get("amount")
            ),
            "as_of": (
                _as_of_date(
                    getattr(r, "as_of", None)
                    if not isinstance(r, dict)
                    else r.get("as_of", r.get("trade_date"))
                )
            ),
        }
        for r in (index_rows or [])
    ]
    source_dates = _source_dates(items, indexes_payload, market_payload)

    return {
        "as_of": source_dates[0] if len(source_dates) == 1 else None,
        "as_of_dates": source_dates,
        "observed_at": datetime.datetime.now(_TZ).isoformat(timespec="seconds"),
        "indexes": indexes_payload,
        "market": market_payload,
        "items": items,
        "meta": {
            "n": len(items),
            "max_n": max_n,
            "truncated": truncated,
            "skip_board": skip_board,
            "board_enabled": do_board,
        },
        "errors": errors,
    }
