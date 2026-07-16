"""行业/主题 ETF 走势状态与规则点评（omg etf）。"""

from __future__ import annotations

import datetime
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import adata
import pandas as pd

BENCH_CODE = "510300"
STATE_START = "启动趋势"
STATE_STRONG = "强趋势"
STATE_WEAKEN = "走弱见顶"
STATE_DOWN = "弱势续跌"
STATE_CHOP = "无效震荡"
STATES = (STATE_START, STATE_STRONG, STATE_WEAKEN, STATE_DOWN, STATE_CHOP)
# 报告分组顺序（与扫描关注优先级一致）
REPORT_ORDER = (STATE_START, STATE_WEAKEN, STATE_STRONG, STATE_DOWN, STATE_CHOP)

HORIZON_SHORT = "short"
HORIZON_MID = "mid"

# MA60 需 ~60 根；150 日历日约 100+ 交易日，留停牌余量
LOOKBACK_CALENDAR_DAYS = 150
MIN_BARS = 70
FEATURE_TAIL = 90  # 对齐后只保留末段再算滚动指标
DEFAULT_TOP_PER_STATE = 8
DEFAULT_WORKERS = 12

_UNIVERSE_PATH = Path(__file__).resolve().parent / "etf_universe.csv"
_EMPTY_COLS = ["trade_date", "close", "amount", "change_pct"]

# 短线 / 中线阈值（同一套判定流水线）
_HORIZON_CFG = {
    HORIZON_SHORT: {
        "ret_key": "ret_10",
        "excess_key": "excess_10",
        "use_ret_5_for_top": True,
        "chop_vol_factor": 0.85,
        "ma_spread_chop": 0.015,
        "abs_ret_chop": 0.03,
        "abs_slope_chop": 0.005,
        "top_range": 0.75,
        "top_ret_hard": -0.02,  # 对 ret_5
        "top_vol": 1.4,
        "break_range": 0.7,
        "start_slope": 0.003,
        "start_excess": 0.005,
        "start_vol": 1.25,
        "start_range_max": 0.9,
        "start_require_not_firm_bull": True,
        "strong_need_excess": True,
        "strong_alt_ret": 0.02,
        "strong_use_ma60": False,
        "down_alt_ret": None,  # 用 slope < 0
    },
    HORIZON_MID: {
        "ret_key": "ret_20",
        "excess_key": "excess_20",
        "use_ret_5_for_top": False,
        "chop_vol_factor": 0.9,
        "ma_spread_chop": 0.02,
        "abs_ret_chop": 0.05,
        "abs_slope_chop": 0.008,
        "top_range": 0.8,
        "top_ret_hard": -0.03,  # 对 ret_20
        "top_vol": 1.3,
        "break_range": 0.65,
        "start_slope": 0.004,
        "start_excess": 0.01,
        "start_vol": 1.15,
        "start_range_max": None,
        "start_require_not_firm_bull": False,
        "strong_need_excess": False,
        "strong_alt_ret": None,
        "strong_use_ma60": True,
        "down_alt_ret": -0.03,
    },
}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def load_universe(path: Optional[str] = None) -> list[dict]:
    csv_path = Path(path) if path else _UNIVERSE_PATH
    df = pd.read_csv(csv_path, dtype={"code": str})
    rows = []
    for rec in df.to_dict("records"):
        code = str(rec.get("code", "")).strip()
        if not code:
            continue
        mcap = rec.get("mcap")
        rows.append(
            {
                "code": code,
                "name": str(rec.get("name", "") or "").strip(),
                "category": str(rec.get("category", "") or "").strip(),
                "theme": str(rec.get("theme", "") or "").strip(),
                "mcap": float(mcap) if mcap is not None and pd.notna(mcap) else 0.0,
            }
        )
    return rows


def history_start_date(calendar_days: int = LOOKBACK_CALENDAR_DAYS) -> str:
    return (datetime.date.today() - datetime.timedelta(days=calendar_days)).strftime(
        "%Y-%m-%d"
    )


def _prep_series(s: pd.Series) -> pd.Series:
    out = pd.to_numeric(s, errors="coerce")
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _align_series(*series_list: pd.Series) -> list[pd.Series]:
    frames = [_prep_series(s) for s in series_list]
    df = pd.concat(frames, axis=1, join="inner").dropna()
    return [df.iloc[:, i] for i in range(df.shape[1])]


def _empty_features() -> dict:
    return {
        "ret_5": None,
        "ret_10": None,
        "ret_20": None,
        "excess_10": None,
        "excess_20": None,
        "ma20": None,
        "ma60": None,
        "ma20_slope": None,
        "above_ma20": None,
        "above_ma60": None,
        "ma_bull": None,
        "ma_bear": None,
        "vol_ratio": None,
        "range_pos_60": None,
        "vol_20": None,
        "vol_60": None,
        "ma_spread": None,
        "close": None,
        "ok": False,
    }


def compute_features(
    close: pd.Series,
    amount: pd.Series,
    bench_close: pd.Series,
) -> dict:
    empty = _empty_features()
    c, a, b = _align_series(close, amount, bench_close)
    if len(c) < MIN_BARS:
        return empty

    if len(c) > FEATURE_TAIL:
        c, a, b = c.iloc[-FEATURE_TAIL:], a.iloc[-FEATURE_TAIL:], b.iloc[-FEATURE_TAIL:]

    ma20 = c.rolling(20, min_periods=20).mean()
    ma60 = c.rolling(60, min_periods=60).mean()
    ret = c.pct_change()

    def last_ret(n: int) -> Optional[float]:
        if len(c) <= n:
            return None
        v = c.iloc[-1] / c.iloc[-1 - n] - 1.0
        return float(v) if math.isfinite(v) else None

    def last_excess(n: int) -> Optional[float]:
        if len(c) <= n:
            return None
        er = c.iloc[-1] / c.iloc[-1 - n] - 1.0
        br = b.iloc[-1] / b.iloc[-1 - n] - 1.0
        v = er - br
        return float(v) if math.isfinite(v) else None

    ma20_now = float(ma20.iloc[-1])
    ma60_now = float(ma60.iloc[-1])
    close_now = float(c.iloc[-1])
    if not all(math.isfinite(x) for x in (ma20_now, ma60_now, close_now)):
        return empty

    if len(ma20) > 5 and pd.notna(ma20.iloc[-6]):
        base = float(ma20.iloc[-6])
        ma20_slope = (ma20_now / base - 1.0) if base else 0.0
    else:
        ma20_slope = 0.0

    amt_5 = a.rolling(5, min_periods=5).mean().iloc[-1]
    amt_20 = a.rolling(20, min_periods=20).mean().iloc[-1]
    if pd.isna(amt_5) or pd.isna(amt_20) or float(amt_20) <= 0:
        vol_ratio = None
    else:
        vol_ratio = float(amt_5) / float(amt_20)

    window60 = c.iloc[-60:]
    low60 = float(window60.min())
    high60 = float(window60.max())
    span = high60 - low60
    range_pos = (close_now - low60) / span if span > 1e-12 else 0.5

    vol_20 = float(ret.iloc[-20:].std()) if len(ret) >= 20 else None
    vol_60 = float(ret.iloc[-60:].std()) if len(ret) >= 60 else None
    ma_spread = abs(ma20_now - ma60_now) / close_now

    return {
        "ret_5": last_ret(5),
        "ret_10": last_ret(10),
        "ret_20": last_ret(20),
        "excess_10": last_excess(10),
        "excess_20": last_excess(20),
        "ma20": ma20_now,
        "ma60": ma60_now,
        "ma20_slope": ma20_slope,
        "above_ma20": close_now > ma20_now,
        "above_ma60": close_now > ma60_now,
        "ma_bull": close_now > ma20_now > ma60_now,
        "ma_bear": close_now < ma20_now < ma60_now,
        "vol_ratio": vol_ratio,
        "range_pos_60": range_pos,
        "vol_20": vol_20,
        "vol_60": vol_60,
        "ma_spread": ma_spread,
        "close": close_now,
        "ok": True,
    }


def classify_horizon(features: dict, horizon: str) -> str:
    if not features or not features.get("ok"):
        return STATE_CHOP

    cfg = _HORIZON_CFG[horizon]
    slope = features.get("ma20_slope") or 0.0
    vol_ratio = features.get("vol_ratio") or 1.0
    range_pos = features.get("range_pos_60") or 0.5
    ma_spread = features.get("ma_spread") or 0.0
    vol_20 = features.get("vol_20")
    vol_60 = features.get("vol_60")
    excess = features.get(cfg["excess_key"]) or 0.0
    ret = features.get(cfg["ret_key"]) or 0.0
    ret_5 = features.get("ret_5") or 0.0
    above_ma20 = features.get("above_ma20")
    ma_bull = features.get("ma_bull")
    ma_bear = features.get("ma_bear")

    chop_vol = (
        vol_20 is not None
        and vol_60 is not None
        and vol_60 > 0
        and vol_20 < vol_60 * cfg["chop_vol_factor"]
    )
    if (
        ma_spread < cfg["ma_spread_chop"]
        and abs(ret) < cfg["abs_ret_chop"]
        and (chop_vol or abs(slope) < cfg["abs_slope_chop"])
    ):
        return STATE_CHOP

    if cfg["use_ret_5_for_top"]:
        top_hit = range_pos >= cfg["top_range"] and (
            ret_5 < cfg["top_ret_hard"] or (ret_5 <= 0 and vol_ratio >= cfg["top_vol"])
        )
    else:
        top_hit = range_pos >= cfg["top_range"] and (
            ret < cfg["top_ret_hard"]
            or (ret <= 0.01 and vol_ratio >= cfg["top_vol"])
        )
    if top_hit:
        return STATE_WEAKEN
    if above_ma20 is False and range_pos >= cfg["break_range"] and slope <= 0:
        return STATE_WEAKEN

    if ma_bear and slope < 0 and ret < 0:
        return STATE_DOWN

    start_ok = (
        above_ma20
        and slope > cfg["start_slope"]
        and excess > cfg["start_excess"]
        and vol_ratio >= cfg["start_vol"]
    )
    if cfg["start_require_not_firm_bull"]:
        start_ok = start_ok and not (ma_bull and range_pos > 0.85)
    if cfg["start_range_max"] is not None:
        start_ok = start_ok and range_pos < cfg["start_range_max"]
    if horizon == HORIZON_MID:
        start_ok = start_ok and not ma_bull
    if start_ok:
        return STATE_START

    if cfg["strong_need_excess"]:
        if ma_bull and slope >= 0 and excess >= 0:
            return STATE_STRONG
        if (
            above_ma20
            and slope > 0
            and cfg["strong_alt_ret"] is not None
            and ret > cfg["strong_alt_ret"]
        ):
            return STATE_STRONG
    else:
        if ma_bull and slope >= 0:
            return STATE_STRONG
        if cfg["strong_use_ma60"] and features.get("above_ma60") and slope > 0 and excess > 0:
            return STATE_STRONG

    if cfg["down_alt_ret"] is None:
        if ma_bear or (not above_ma20 and slope < 0):
            return STATE_DOWN
    else:
        if ma_bear or (not above_ma20 and ret < cfg["down_alt_ret"]):
            return STATE_DOWN

    return STATE_CHOP


def classify_etf(
    close: pd.Series,
    amount: pd.Series,
    bench_close: pd.Series,
) -> dict:
    feat = compute_features(close, amount, bench_close)
    return {
        "short_state": classify_horizon(feat, HORIZON_SHORT),
        "mid_state": classify_horizon(feat, HORIZON_MID),
        "features": feat,
        "available": bool(feat.get("ok")),
    }


def _fmt_chg(chg) -> str:
    if isinstance(chg, (int, float)) and chg == chg:
        return f"{chg:+.2f}%"
    return "-"


def build_comment(row: dict) -> str:
    feat = row.get("features") or {}
    short = row.get("short_state") or ""
    mid = row.get("mid_state") or ""
    parts = []

    vol_ratio = feat.get("vol_ratio")
    excess_10 = feat.get("excess_10")
    range_pos = feat.get("range_pos_60")
    slope = feat.get("ma20_slope")

    if vol_ratio is not None:
        if vol_ratio >= 1.5:
            parts.append("近5日明显放量")
        elif vol_ratio >= 1.25:
            parts.append("温和放量")
        elif vol_ratio <= 0.75:
            parts.append("量能收缩")
        else:
            parts.append("量能平稳")

    if excess_10 is not None:
        if excess_10 >= 0.03:
            parts.append("相对沪深300显著超额")
        elif excess_10 >= 0.01:
            parts.append("略有超额")
        elif excess_10 <= -0.03:
            parts.append("相对大盘明显落后")
        elif excess_10 <= -0.01:
            parts.append("略弱于大盘")

    short_text = {
        STATE_START: "短线均线转多、有启动迹象",
        STATE_STRONG: "短线多头延续",
        STATE_WEAKEN: "高位走弱需警惕",
        STATE_DOWN: "空头格局延续",
        STATE_CHOP: "方向不清、偏震荡",
    }
    if short in short_text:
        parts.append(short_text[short])

    if mid != short:
        parts.append(f"中线偏{mid}")

    if range_pos is not None and range_pos >= 0.85 and short in (STATE_STRONG, STATE_START):
        parts.append("已处60日高位区")
    if slope is not None and slope < -0.01:
        parts.append("均线下行")

    chg = row.get("change_pct")
    if isinstance(chg, (int, float)) and chg == chg:
        parts.append(f"当日{_fmt_chg(chg)}")

    if not parts:
        return "数据不足，暂无点评"
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "；".join(out[:5])


def fetch_etf_frame(fund_code: str, start_date: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=_EMPTY_COLS)
    try:
        df = adata.fund.market.get_market_etf(
            fund_code=str(fund_code), k_type=1, start_date=start_date
        )
    except Exception:
        return empty
    if df is None or df.empty:
        return empty

    out = df.copy()
    out["trade_date"] = out["trade_date"].astype(str).str[:10]
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
    if "change_pct" in out.columns:
        out["change_pct"] = pd.to_numeric(out["change_pct"], errors="coerce")
    else:
        out["change_pct"] = pd.NA
    out = out.dropna(subset=["close"])
    out = out.drop_duplicates(subset=["trade_date"], keep="last")
    out = out.loc[out["trade_date"] >= start_date]
    return out.sort_values("trade_date").reset_index(drop=True)


def _frame_ohlc_series(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    if frame is None or frame.empty:
        empty = pd.Series(dtype=float)
        return empty, empty
    indexed = frame.set_index("trade_date")
    indexed.index = pd.to_datetime(indexed.index)
    close = pd.to_numeric(indexed["close"], errors="coerce").sort_index()
    amount = (
        pd.to_numeric(indexed["amount"], errors="coerce").sort_index()
        if "amount" in indexed.columns
        else pd.Series(dtype=float)
    )
    return close, amount


def _parse_change_pct(value) -> Optional[float]:
    try:
        return float(value) if pd.notna(value) else None
    except (TypeError, ValueError):
        return None


def analyze_one(meta: dict, bench_close: pd.Series, start_date: str) -> dict:
    code = meta["code"]
    row = {
        "code": code,
        "name": meta.get("name", ""),
        "theme": meta.get("theme", ""),
        "category": meta.get("category", ""),
        "available": False,
        "short_state": STATE_CHOP,
        "mid_state": STATE_CHOP,
        "change_pct": None,
        "features": {},
        "comment": "行情不足",
    }
    frame = fetch_etf_frame(code, start_date)
    if frame.empty:
        return row

    close, amount = _frame_ohlc_series(frame)
    result = classify_etf(close, amount, bench_close)
    row.update(
        {
            "available": result["available"],
            "short_state": result["short_state"],
            "mid_state": result["mid_state"],
            "change_pct": _parse_change_pct(frame.iloc[-1].get("change_pct")),
            "features": result["features"],
        }
    )
    row["comment"] = build_comment(row)
    return row


def _sort_key_for_state(state: str, row: dict):
    feat = row.get("features") or {}
    excess = feat.get("excess_10")
    chg = row.get("change_pct")
    if state in (STATE_START, STATE_STRONG):
        return (excess if excess is not None else -999, chg if chg is not None else -999)
    if state in (STATE_WEAKEN, STATE_DOWN):
        return (excess if excess is not None else 999, chg if chg is not None else 999)
    ret10 = feat.get("ret_10") or 0.0
    return (abs(ret10),)


def select_for_report(rows: list[dict], top_per_state: int = DEFAULT_TOP_PER_STATE) -> list[dict]:
    selected = []
    for state in REPORT_ORDER:
        group = [r for r in rows if r.get("short_state") == state]
        reverse = state in (STATE_START, STATE_STRONG)
        group.sort(key=lambda r: _sort_key_for_state(state, r), reverse=reverse)
        selected.extend(group[:top_per_state])
    return selected


def scan_etf_states(
    top_per_state: int = DEFAULT_TOP_PER_STATE,
    workers: Optional[int] = None,
    universe_path: Optional[str] = None,
) -> list[dict]:
    if workers is None:
        workers = _env_int("OMG_ETF_WORKERS", DEFAULT_WORKERS)
    max_n = _env_int("OMG_ETF_MAX", 0) or None

    universe = load_universe(universe_path)
    if max_n is not None:
        universe = sorted(universe, key=lambda u: u.get("mcap") or 0, reverse=True)[
            :max_n
        ]

    start = history_start_date()
    print(f"获取对照宽基 {BENCH_CODE} ...")
    bench_frame = fetch_etf_frame(BENCH_CODE, start)
    bench_close = _prep_series(_frame_ohlc_series(bench_frame)[0])
    if bench_close.empty:
        print("宽基行情获取失败")
        return []

    n_workers = min(workers, max(1, len(universe)))
    print(f"扫描主题 ETF（{len(universe)} 只，短线+中线）...")
    rows = []
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futs = {
            ex.submit(analyze_one, u, bench_close, start): u["code"] for u in universe
        }
        done = 0
        for fut in as_completed(futs):
            done += 1
            if done % 20 == 0 or done == len(universe):
                print(f"  进度 {done}/{len(universe)}")
            try:
                rows.append(fut.result())
            except Exception:
                continue

    available = [r for r in rows if r.get("available")]
    print(f"有效样本 {len(available)}/{len(rows)}")
    return select_for_report(available, top_per_state=top_per_state)


def print_etf_report(rows: list[dict]) -> None:
    if not rows:
        print("无可用 ETF 状态结果")
        return

    print("\n=== 主题 ETF 走势点评（短线+中线）===")
    print("说明: 宇宙=每主题规模最大1只；对照=510300 沪深300ETF。")
    print("      短线≈5-20日，中线≈20-60日；点评为规则生成，非预测。\n")

    by_state: dict[str, list] = {}
    for r in rows:
        by_state.setdefault(r["short_state"], []).append(r)

    for state in REPORT_ORDER:
        group = by_state.get(state) or []
        if not group:
            continue
        print(f"【{state}】{len(group)} 只")
        for i, r in enumerate(group, 1):
            feat = r.get("features") or {}
            excess = feat.get("excess_10")
            excess_s = f"{excess * 100:+.1f}%" if isinstance(excess, float) else "-"
            print(
                f"{i}. {r['theme']}  {r['code']} {r['name']}  "
                f"短线:{r['short_state']} 中线:{r['mid_state']}  "
                f"涨跌:{_fmt_chg(r.get('change_pct'))} 超额10日:{excess_s}"
            )
            print(f"   {r.get('comment') or ''}")
        print()
