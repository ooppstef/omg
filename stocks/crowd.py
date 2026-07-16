import datetime
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import adata
import pandas as pd
import requests

# 行业拥挤度：东财行业板块（BK）。
# 历史 K 线走 push2his；若当前网络不可用，则用「同名」同花顺行业指数（881）作成交额代理
#（实时额已验证与东财高度接近，如银行/半导体）。

LOOKBACK_YEARS = 3
MAX_HISTORY_YEARS = 10
SHARE_SMOOTH_DAYS = 5
MIN_HISTORY_DAYS = 120
TRADING_DAYS_PER_YEAR = 250

LOOKBACK_DAYS = LOOKBACK_YEARS * TRADING_DAYS_PER_YEAR  # 750
MAX_HISTORY_DAYS = MAX_HISTORY_YEARS * TRADING_DAYS_PER_YEAR  # 2500

SH_INDEX = "000001"
SZ_INDEX = "399001"

INDUSTRY_EXCLUDE = set()

_SESSION = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.trust_env = False  # 避开坏掉的本地代理；东财 delay 主机可直连
        s.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            }
        )
        _SESSION = s
    return _SESSION


def compute_share(concept_amount, market_amount) -> Optional[float]:
    try:
        c = float(concept_amount)
        m = float(market_amount)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(c) or not math.isfinite(m) or m <= 0:
        return None
    return c / m


def rolling_mean(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def percentile_rank(value: float, history: pd.Series) -> Optional[float]:
    hist = pd.to_numeric(history, errors="coerce").dropna()
    if hist.empty or value is None or not math.isfinite(float(value)):
        return None
    return float((hist < float(value)).sum() / len(hist) * 100.0)


def crowding_from_shares(share: pd.Series, as_of=None) -> dict:
    """
    share: index 为交易日，值为日份额。
    返回: available, share, share_5d, crowd_pct, history_days
    """
    empty = {
        "available": False,
        "share": None,
        "share_5d": None,
        "crowd_pct": None,
        "history_days": 0,
    }
    if share is None or share.empty:
        return empty

    s = share.copy()
    s.index = pd.to_datetime(s.index)
    s = pd.to_numeric(s, errors="coerce")
    s = s.sort_index()
    if len(s) > MAX_HISTORY_DAYS:
        s = s.iloc[-MAX_HISTORY_DAYS:]

    if as_of is not None:
        as_of = pd.to_datetime(as_of)
        s = s.loc[s.index <= as_of]
    if s.empty:
        return empty

    share_5d = rolling_mean(s, SHARE_SMOOTH_DAYS)
    last = s.iloc[-1]
    today_share = float(last) if pd.notna(last) and math.isfinite(float(last)) else None
    today_5d = share_5d.iloc[-1]
    if pd.isna(today_5d):
        return {**empty, "share": today_share}

    window = share_5d.dropna().iloc[-LOOKBACK_DAYS:]
    # 分位历史不含当日；有效历史样本须 ≥ MIN_HISTORY_DAYS
    hist = window.iloc[:-1] if len(window) > 1 else window.iloc[0:0]
    if len(hist) < MIN_HISTORY_DAYS:
        return {
            "available": False,
            "share": today_share,
            "share_5d": float(today_5d),
            "crowd_pct": None,
            "history_days": len(hist),
        }

    pct = percentile_rank(float(today_5d), hist)
    return {
        "available": pct is not None,
        "share": today_share,
        "share_5d": float(today_5d),
        "crowd_pct": None if pct is None else round(pct, 1),
        "history_days": len(hist),
    }


def history_start_date(years: int = MAX_HISTORY_YEARS) -> str:
    days = years * 365 + 30
    return (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "amount", "change_pct"])
    out = df.copy()
    out["trade_date"] = out["trade_date"].astype(str).str[:10]
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
    if "change_pct" in out.columns:
        out["change_pct"] = pd.to_numeric(out["change_pct"], errors="coerce")
    else:
        out["change_pct"] = pd.NA
    out = out.dropna(subset=["amount"])
    out = out.drop_duplicates(subset=["trade_date"], keep="last")
    return out.sort_values("trade_date")


def _to_amount_series(df: pd.DataFrame) -> pd.Series:
    out = _normalize_ohlcv(df)
    if out.empty:
        return pd.Series(dtype=float)
    return out.set_index("trade_date")["amount"].sort_index()


def fetch_market_amount(start_date: str) -> pd.Series:
    sh = adata.stock.market.get_market_index(index_code=SH_INDEX, start_date=start_date)
    sz = adata.stock.market.get_market_index(index_code=SZ_INDEX, start_date=start_date)
    sh_s = _to_amount_series(sh)
    sz_s = _to_amount_series(sz)
    aligned = pd.concat([sh_s, sz_s], axis=1, join="inner")
    aligned.columns = ["sh", "sz"]
    return (aligned["sh"] + aligned["sz"]).rename("market_amount")


def _clip_history(out: pd.DataFrame, start_date: str) -> pd.DataFrame:
    if out.empty:
        return out
    out = out.loc[out["trade_date"] >= start_date]
    if len(out) > MAX_HISTORY_DAYS:
        out = out.iloc[-MAX_HISTORY_DAYS:]
    return out.reset_index(drop=True)


def fetch_board_frame_em(bk_code: str, start_date: str) -> pd.DataFrame:
    """东财行业日线（push2his）。失败返回空表。"""
    code = str(bk_code).strip().upper()
    if not code.startswith("BK"):
        return pd.DataFrame(columns=["trade_date", "amount", "change_pct"])
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"90.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "end": "20500101",
        "lmt": str(MAX_HISTORY_DAYS + 50),
    }
    try:
        r = _session().get(url, params=params, timeout=12)
        data = (r.json() or {}).get("data") or {}
        klines = data.get("klines") or []
    except Exception:
        return pd.DataFrame(columns=["trade_date", "amount", "change_pct"])
    if not klines:
        return pd.DataFrame(columns=["trade_date", "amount", "change_pct"])

    rows = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 11:
            continue
        rows.append(
            {
                "trade_date": parts[0][:10],
                "amount": parts[6],
                "change_pct": parts[8],
            }
        )
    return _clip_history(_normalize_ohlcv(pd.DataFrame(rows)), start_date)


def fetch_board_frame_ths(index_code: str, start_date: str) -> pd.DataFrame:
    """同花顺行业/板块日线；失败重试一次。"""
    df = None
    for _ in range(2):
        try:
            df = adata.stock.market.get_market_concept_ths(index_code=index_code)
            if df is not None and not df.empty:
                break
        except Exception:
            df = None
    return _clip_history(_normalize_ohlcv(df), start_date)


def fetch_board_frame(
    index_code: str,
    start_date: str,
    ths_code: Optional[str] = None,
) -> pd.DataFrame:
    """
    优先东财历史；不通则用同名同花顺行业指数。
    index_code: 东财 BK 代码（主身份）。
    """
    em = fetch_board_frame_em(index_code, start_date)
    if not em.empty:
        return em
    if ths_code:
        return fetch_board_frame_ths(ths_code, start_date)
    return em


def fetch_concept_frame(index_code: str, start_date: str) -> pd.DataFrame:
    """兼容旧名。"""
    return fetch_board_frame(index_code, start_date)


def fetch_concept_amount(index_code: str, start_date: str) -> pd.Series:
    frame = fetch_board_frame(index_code, start_date)
    if frame.empty:
        return pd.Series(dtype=float)
    return frame.set_index("trade_date")["amount"].sort_index()


def _list_em_industries() -> dict:
    """东财全部行业板块 name -> BK 代码。"""
    out = {}
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
            diff = ((r.json() or {}).get("data") or {}).get("diff") or []
        except Exception:
            break
        if not diff:
            break
        for row in diff:
            name = str(row.get("f14") or "").strip()
            code = str(row.get("f12") or "").strip().upper()
            if name and code.startswith("BK"):
                out[name] = code
    return out


def _list_ths_industries() -> dict:
    """同花顺行业 name -> 881 代码（独立 session，避免与东财 cookie 互相干扰）。"""
    out = {}
    s = requests.Session()
    s.trust_env = False
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "http://q.10jqka.com.cn/thshy/",
    }
    for page in range(1, 6):
        url = (
            "http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/"
            f"page/{page}/ajax/1/"
        )
        try:
            r = s.get(url, headers=headers, timeout=12)
            if r.status_code != 200:
                break
            html = r.text
        except Exception:
            break
        # <a href=".../thshy/detail/code/881155/">银行</a>
        pairs = re.findall(
            r'href="[^"]*?/thshy/detail/code/(\d+)/?"[^>]*>([^<]+)<',
            html,
        )
        if not pairs:
            break
        for code, name in pairs:
            name = name.strip()
            if name and code.startswith("881"):
                out[name] = code
        # 轻微间隔，降低翻页 401
        if page < 5:
            time.sleep(0.35)
    return out


def list_industries() -> pd.DataFrame:
    """
    东财行业 ∩ 同花顺行业（同名），约 50+ 个产业板块。
    列: name, code(东财BK), ths_code。
    先拉同花顺再拉东财，避免同花顺翻页被限流。
    """
    ths = _list_ths_industries()
    em = _list_em_industries()
    if not em or not ths:
        return pd.DataFrame(columns=["name", "code", "ths_code"])

    rows = []
    for name, bk in sorted(em.items(), key=lambda x: x[0]):
        ths_code = ths.get(name)
        if not ths_code:
            continue
        # 去掉罗马数字细分名（如白酒Ⅲ）——同名交集里本身已是干净产业名
        if name.endswith(("Ⅰ", "Ⅱ", "Ⅲ", "I", "II", "III")):
            continue
        rows.append({"name": name, "code": bk, "ths_code": ths_code})
    return pd.DataFrame(rows)


def build_share_series(concept_amount: pd.Series, market_amount: pd.Series) -> pd.Series:
    c = pd.to_numeric(concept_amount, errors="coerce")
    m = pd.to_numeric(market_amount, errors="coerce")
    c.index = c.index.astype(str).str[:10]
    m.index = m.index.astype(str).str[:10]
    df = pd.concat([c.rename("c"), m.rename("m")], axis=1, join="inner")
    df = df[(df["m"] > 0) & df["c"].notna()]
    return (df["c"] / df["m"]).rename("share")


def _env_int(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _env_max_boards():
    """None=不截断；正整数=最多扫描 N 个行业。兼容旧环境变量名。"""
    for key in ("OMG_CROWD_MAX_INDUSTRIES", "OMG_CROWD_MAX_CONCEPTS"):
        value = os.getenv(key)
        if value is None or str(value).strip() == "":
            continue
        try:
            parsed = int(value)
        except ValueError:
            continue
        return parsed if parsed > 0 else None
    return None


def compute_board_crowding(
    index_code: str,
    board_name: str,
    market_amount: pd.Series,
    ths_code: Optional[str] = None,
) -> dict:
    start = history_start_date(MAX_HISTORY_YEARS)
    frame = fetch_board_frame(index_code, start, ths_code=ths_code)
    if frame.empty:
        return {
            "index_code": index_code,
            "ths_code": ths_code,
            "board_name": board_name,
            "concept_name": board_name,
            "change_pct": None,
            "available": False,
            "share": None,
            "share_5d": None,
            "crowd_pct": None,
            "history_days": 0,
        }
    board_amt = frame.set_index("trade_date")["amount"].sort_index()
    share = build_share_series(board_amt, market_amount)
    base = crowding_from_shares(share)
    change_pct = frame.iloc[-1]["change_pct"]
    try:
        change_pct = float(change_pct) if pd.notna(change_pct) else None
    except (TypeError, ValueError):
        change_pct = None
    return {
        "index_code": index_code,
        "ths_code": ths_code,
        "board_name": board_name,
        "concept_name": board_name,
        "change_pct": change_pct,
        **base,
    }


def compute_concept_crowding(index_code: str, concept_name: str, market_amount: pd.Series) -> dict:
    """兼容旧名。"""
    return compute_board_crowding(index_code, concept_name, market_amount)


def rank_crowding_rows(rows: list, top: int = 15) -> list:
    ok = [r for r in rows if r.get("available") and r.get("crowd_pct") is not None]
    ok.sort(key=lambda r: r["crowd_pct"], reverse=True)
    return ok[:top]


def _fetch_em_constituents(bk_code: str) -> list:
    """东财行业成分股 [(code, name), ...]。"""
    code = str(bk_code).strip().upper()
    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    rows = []
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
            diff = ((r.json() or {}).get("data") or {}).get("diff") or []
        except Exception:
            break
        if not diff:
            break
        for item in diff:
            stock = str(item.get("f12") or "").strip()
            name = str(item.get("f14") or "").strip()
            if stock:
                rows.append((stock, name))
        if len(diff) < 100:
            break
    return rows


def _fetch_concept_top_stocks(index_code: str, top_n: int = 3) -> list:
    """代表股：东财成分股中按当日成交额优先，其次涨跌幅；排除 ST / 北交所。"""
    constituents = _fetch_em_constituents(index_code)
    if not constituents:
        # 兜底：若有 ths_code 场景外单独调用时，尝试同花顺成分
        try:
            df = adata.stock.info.concept_constituent_ths(index_code=index_code)
        except Exception:
            return []
        if df is None or df.empty or "stock_code" not in df.columns:
            return []
        constituents = [
            (str(r.get("stock_code", "")).strip(), str(r.get("short_name", "") or ""))
            for _, r in df.iterrows()
        ]

    valid_prefixes = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "688")
    name_by_code = {}
    codes = []
    for code, name in constituents:
        if not code.startswith(valid_prefixes):
            continue
        if "ST" in name.upper():
            continue
        codes.append(code)
        name_by_code[code] = name
    if not codes:
        return []

    quote_map = {}
    try:
        quote = adata.stock.market.list_market_current(codes)
        if quote is not None and not quote.empty:
            for _, row in quote.iterrows():
                code = str(row.get("stock_code", "")).strip()
                name = str(row.get("short_name", "") or name_by_code.get(code, ""))
                if "ST" in name.upper():
                    continue
                try:
                    amount = float(row.get("amount", 0) or 0)
                except (TypeError, ValueError):
                    amount = 0.0
                try:
                    price = float(row.get("price", 0) or 0)
                except (TypeError, ValueError):
                    price = 0.0
                try:
                    chg = row.get("change_pct")
                    change_pct = None if pd.isna(chg) else float(chg)
                except (TypeError, ValueError):
                    change_pct = None
                valid = price > 0 or amount > 0
                quote_map[code] = {
                    "code": code,
                    "name": name or name_by_code.get(code, code),
                    "amount": amount,
                    "change_pct": change_pct if valid else None,
                    "valid": valid,
                }
    except Exception:
        quote_map = {}

    rows = list(quote_map.values())
    valid_rows = [r for r in rows if r.get("valid")]
    if valid_rows:
        if any(r["amount"] > 0 for r in valid_rows):
            valid_rows.sort(key=lambda x: (x["amount"], x["change_pct"] or -999), reverse=True)
        else:
            valid_rows.sort(
                key=lambda x: (x["change_pct"] is not None, x["change_pct"] or -999),
                reverse=True,
            )
        return [
            {"code": r["code"], "name": r["name"], "amount": r["amount"], "change_pct": r["change_pct"]}
            for r in valid_rows[:top_n]
        ]

    fallback = []
    for code in codes[: top_n * 3]:
        name = name_by_code.get(code, code)
        if "ST" in name.upper():
            continue
        fallback.append({"code": code, "name": name, "amount": 0.0, "change_pct": None})
        if len(fallback) >= top_n:
            break
    return fallback


def attach_top_stocks(rows: list, top_n: int = 3, workers: int = 8) -> list:
    """仅为排行结果拉取代表股，避免全市场成分股请求。"""
    if not rows:
        return rows

    def one(row):
        stocks = _fetch_concept_top_stocks(row["index_code"], top_n=top_n)
        out = dict(row)
        out["top_stocks"] = stocks
        return out

    enriched = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=min(workers, len(rows))) as ex:
        futs = {ex.submit(one, row): idx for idx, row in enumerate(rows)}
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                enriched[idx] = fut.result()
            except Exception:
                out = dict(rows[idx])
                out["top_stocks"] = []
                enriched[idx] = out
    return enriched


def scan_concept_crowding(top: int = 15, workers=None, exclude=None, top_stocks: int = 3) -> list:
    """扫描东财行业拥挤度（函数名保留以兼容 CLI）。"""
    if workers is None:
        workers = _env_int("OMG_CROWD_WORKERS", 8)
    max_n = _env_max_boards()

    exclude_set = INDUSTRY_EXCLUDE | set(exclude or [])
    start = history_start_date(MAX_HISTORY_YEARS)
    print("获取全市场成交额...")
    market_amount = fetch_market_amount(start)
    if market_amount.empty:
        print("全市场成交额获取失败")
        return []

    print("获取东财行业列表（与同花顺同名交集）...")
    industry_df = list_industries()
    if industry_df is None or industry_df.empty:
        print("获取行业列表失败（东财或同花顺列表不可用）")
        return []

    tasks = []
    for _, row in industry_df.iterrows():
        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip().upper()
        ths_code = str(row.get("ths_code", "")).strip()
        if not name or not code or name in exclude_set:
            continue
        if not code.startswith("BK"):
            continue
        tasks.append((code, name, ths_code or None))
        if max_n is not None and len(tasks) >= max_n:
            break

    if not tasks:
        print(f"无可用行业（原始 {len(industry_df)} 条）")
        return []

    print(f"计算拥挤度（东财行业 {len(tasks)}，近3年分位）...")
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(compute_board_crowding, code, name, market_amount, ths_code): name
            for code, name, ths_code in tasks
        }
        done = 0
        for fut in as_completed(futs):
            done += 1
            if done % 20 == 0 or done == len(tasks):
                print(f"  进度 {done}/{len(tasks)}")
            try:
                rows.append(fut.result())
            except Exception:
                continue
    available_n = sum(1 for r in rows if r.get("available"))
    print(f"有效拥挤度样本 {available_n}/{len(rows)}")
    ranked = rank_crowding_rows(rows, top=top)
    if top_stocks > 0 and ranked:
        print(f"拉取 Top{len(ranked)} 行业的代表股（东财成分）...")
        ranked = attach_top_stocks(ranked, top_n=top_stocks, workers=min(8, workers))
    return ranked


def _fmt_chg(chg) -> str:
    if isinstance(chg, (int, float)) and chg == chg:
        return f"{chg:+.2f}%"
    return "-"


def _fmt_stocks(stocks: list) -> str:
    if not stocks:
        return "（暂无代表股行情）"
    parts = []
    for s in stocks:
        name = s.get("name") or s.get("code") or "?"
        parts.append(f"{name} {_fmt_chg(s.get('change_pct'))}")
    return "、".join(parts)


def print_crowding_table(rows: list) -> None:
    if not rows:
        print("无可用拥挤度结果")
        return

    print(f"\n=== 行业交易拥挤度 Top{len(rows)}（东财行业 · 3年分位）===")
    print("说明: 额占比 = 该行业指数成交额 / (上证+深证成交额)。")
    print("      拥挤分位 = 近5日额占比 在近3年历史中的百分位（100%=近3年新高）。")
    print("      代表股 = 东财行业成分股中成交额最高的3只（无成交额时改按涨跌幅）。")
    print("      注: 东财历史K线不可用时，成交额序列用同名同花顺行业指数代理。\n")

    for i, r in enumerate(rows, 1):
        name = r.get("board_name") or r.get("concept_name") or "?"
        share_pct = (r["share"] or 0) * 100
        share5_pct = (r["share_5d"] or 0) * 100
        crowd = r["crowd_pct"]
        ath = " 近3年新高" if crowd is not None and crowd >= 99.95 else ""
        print(
            f"{i}. {name}  "
            f"拥挤 {crowd:.1f}%{ath}  "
            f"额占比 {share_pct:.2f}%  "
            f"5日额占比 {share5_pct:.2f}%  "
            f"涨跌 {_fmt_chg(r.get('change_pct'))}"
        )
        print(f"   代表股: {_fmt_stocks(r.get('top_stocks') or [])}")
