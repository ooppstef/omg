"""行业/主题 ETF 走势状态与规则点评（omg etf）。"""

from __future__ import annotations

import datetime
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import adata
import pandas as pd
import requests

from stocks.util import env_int, finite_float, fmt_chg

BENCH_CODE = "510300"
STATE_START = "启动趋势"
STATE_STRONG = "强趋势"
STATE_WEAKEN = "见顶迹象"
STATE_DOWN = "空头趋势"
STATE_CHOP = "无效震荡"
STATES = (STATE_START, STATE_STRONG, STATE_WEAKEN, STATE_DOWN, STATE_CHOP)
# 报告分组顺序（与扫描关注优先级一致）
REPORT_ORDER = (STATE_START, STATE_WEAKEN, STATE_DOWN, STATE_STRONG, STATE_CHOP)

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
_EM_SESSION: Optional[requests.Session] = None
_EM_SESSION_LOCK = threading.Lock()
CHANGE_SOURCE_ADATA = "adata"
CHANGE_SOURCE_EM_SPOT = "eastmoney_spot"
# 补齐价相对昨收允许偏离；超出则视为东财异常，放弃补齐
_SPOT_MAX_DEV = 0.25

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
        "top_range": 0.65,
        "top_ret_hard": -0.015,  # 对 ret_5
        "top_vol": 1.3,
        "break_range": 0.55,
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
        "top_range": 0.7,
        "top_ret_hard": -0.02,  # 对 ret_20
        "top_vol": 1.25,
        "break_range": 0.55,
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


def _pct(v: Optional[float]) -> str:
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "-"
    return f"{v * 100:+.1f}%"


def _num(v: Optional[float], digits: int = 2) -> str:
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "-"
    return f"{v:.{digits}f}"


def _feature_number(features: dict, key: str, default: float) -> float:
    number = finite_float(features.get(key))
    return default if number is None else number


def classify_horizon(features: dict, horizon: str) -> tuple[str, list[str]]:
    """返回 (状态, 理由列表)。理由带关键数字，便于核对。"""
    label = "短线" if horizon == HORIZON_SHORT else "中线"
    if not features or not features.get("ok"):
        return STATE_CHOP, [f"{label}数据不足，默认无效震荡"]

    cfg = _HORIZON_CFG[horizon]
    slope = _feature_number(features, "ma20_slope", 0.0)
    vol_ratio = _feature_number(features, "vol_ratio", 1.0)
    range_pos = _feature_number(features, "range_pos_60", 0.5)
    ma_spread = _feature_number(features, "ma_spread", 0.0)
    vol_20 = features.get("vol_20")
    vol_60 = features.get("vol_60")
    excess = _feature_number(features, cfg["excess_key"], 0.0)
    ret = _feature_number(features, cfg["ret_key"], 0.0)
    ret_5 = _feature_number(features, "ret_5", 0.0)
    above_ma20 = features.get("above_ma20")
    ma_bull = features.get("ma_bull")
    ma_bear = features.get("ma_bear")
    ret_name = "10日" if cfg["ret_key"] == "ret_10" else "20日"
    excess_name = "10日超额" if cfg["excess_key"] == "excess_10" else "20日超额"

    def metrics() -> list[str]:
        return [
            f"价{'站上' if above_ma20 else '跌破'}MA20",
            f"MA20斜率{_pct(slope)}",
            f"60日位置{_num(range_pos * 100, 0)}%",
            f"{ret_name}涨跌{_pct(ret)}",
            f"{excess_name}{_pct(excess)}",
            f"量比(5/20日){_num(vol_ratio)}",
        ]

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
        why = [
            f"{label}均线缠绕(MA20/60间距{_pct(ma_spread)})",
            f"{ret_name}涨跌不大({_pct(ret)})",
        ]
        if chop_vol:
            why.append("波动收敛")
        else:
            why.append(f"均线斜率平缓({_pct(slope)})")
        return STATE_CHOP, why

    if cfg["use_ret_5_for_top"]:
        top_hit = range_pos >= cfg["top_range"] and (
            ret_5 < cfg["top_ret_hard"] or (ret_5 <= 0 and vol_ratio >= cfg["top_vol"])
        )
        top_detail = []
        if range_pos >= cfg["top_range"]:
            top_detail.append(f"处60日偏高位({_num(range_pos * 100, 0)}%)")
        if ret_5 < cfg["top_ret_hard"]:
            top_detail.append(f"近5日回落({_pct(ret_5)})")
        elif ret_5 <= 0 and vol_ratio >= cfg["top_vol"]:
            top_detail.append(f"高位滞涨放量(5日{_pct(ret_5)}，量比{_num(vol_ratio)})")
    else:
        top_hit = range_pos >= cfg["top_range"] and (
            ret < cfg["top_ret_hard"]
            or (ret <= 0.01 and vol_ratio >= cfg["top_vol"])
        )
        top_detail = []
        if range_pos >= cfg["top_range"]:
            top_detail.append(f"处60日偏高位({_num(range_pos * 100, 0)}%)")
        if ret < cfg["top_ret_hard"]:
            top_detail.append(f"{ret_name}明显回落({_pct(ret)})")
        elif ret <= 0.01 and vol_ratio >= cfg["top_vol"]:
            top_detail.append(f"高位滞涨({ret_name}{_pct(ret)}，量比{_num(vol_ratio)})")

    if top_hit:
        return STATE_WEAKEN, [f"{label}见顶：" + "；".join(top_detail)] + metrics()[:3]

    if above_ma20 is False and range_pos >= cfg["break_range"] and slope <= 0:
        return STATE_WEAKEN, [
            f"{label}见顶：跌破MA20且均线走平/下行",
            f"60日位置仍偏高({_num(range_pos * 100, 0)}%)",
            f"MA20斜率{_pct(slope)}",
            f"{excess_name}{_pct(excess)}",
        ]

    if (
        above_ma20 is False
        and excess < -0.04
        and range_pos >= 0.45
        and slope <= 0
        and ret < 0
    ):
        return STATE_WEAKEN, [
            f"{label}见顶：破位且显著跑输大盘",
            f"跌破MA20，{excess_name}{_pct(excess)}",
            f"60日位置{_num(range_pos * 100, 0)}%，{ret_name}{_pct(ret)}",
        ]

    if ma_bear and slope < 0 and ret < 0:
        return STATE_DOWN, [
            f"{label}空头：价格<MA20<MA60（空头排列）",
            f"MA20下行({_pct(slope)})，{ret_name}{_pct(ret)}",
            f"{excess_name}{_pct(excess)}，量比{_num(vol_ratio)}",
        ]

    start_ok = (
        above_ma20
        and slope > cfg["start_slope"]
        and excess > cfg["start_excess"]
        and vol_ratio >= cfg["start_vol"]
    )
    start_block = []
    if cfg["start_require_not_firm_bull"] and (ma_bull and range_pos > 0.85):
        start_ok = False
        start_block.append("已是牢固多头高位，不算启动")
    if cfg["start_range_max"] is not None and not (range_pos < cfg["start_range_max"]):
        start_ok = False
    if horizon == HORIZON_MID and ma_bull:
        start_ok = False
    if start_ok:
        return STATE_START, [
            f"{label}启动：站上MA20 + 均线转多 + 超额转正 + 放量",
            f"MA20斜率{_pct(slope)}（阈值>{_pct(cfg['start_slope'])}）",
            f"{excess_name}{_pct(excess)}，量比{_num(vol_ratio)}",
            f"60日位置{_num(range_pos * 100, 0)}%（尚未极端高位）",
        ]

    if cfg["strong_need_excess"]:
        if ma_bull and slope >= 0 and excess >= 0:
            return STATE_STRONG, [
                f"{label}强趋势：多头排列(价>MA20>MA60)且未跑输大盘",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess)}",
                f"60日位置{_num(range_pos * 100, 0)}%，量比{_num(vol_ratio)}",
            ]
        if (
            above_ma20
            and slope > 0
            and cfg["strong_alt_ret"] is not None
            and ret > cfg["strong_alt_ret"]
        ):
            return STATE_STRONG, [
                f"{label}强趋势：站上MA20且{ret_name}涨幅较大({_pct(ret)})",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess)}",
            ]
    else:
        if ma_bull and slope >= 0:
            return STATE_STRONG, [
                f"{label}强趋势：多头排列且均线未下行",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess)}",
                f"60日位置{_num(range_pos * 100, 0)}%",
            ]
        if cfg["strong_use_ma60"] and features.get("above_ma60") and slope > 0 and excess > 0:
            return STATE_STRONG, [
                f"{label}强趋势：站上MA60 + 均线向上 + 超额为正",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess)}",
            ]

    if cfg["down_alt_ret"] is None:
        if ma_bear or (not above_ma20 and slope < 0):
            why = [f"{label}空头："]
            if ma_bear:
                why[0] += "空头排列"
            else:
                why[0] += "跌破MA20且均线下行"
            return STATE_DOWN, why + [
                f"MA20斜率{_pct(slope)}，{ret_name}{_pct(ret)}",
                f"{excess_name}{_pct(excess)}",
            ]
    else:
        if ma_bear or (not above_ma20 and ret < cfg["down_alt_ret"]):
            why = [f"{label}空头："]
            if ma_bear:
                why[0] += "空头排列"
            else:
                why[0] += f"跌破MA20且{ret_name}弱势({_pct(ret)})"
            return STATE_DOWN, why + [
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess)}",
            ]

    why = [f"{label}未满足明确多空条件，判为震荡"] + metrics()[:4]
    if start_block:
        why.extend(start_block)
    return STATE_CHOP, why


def classify_etf(
    close: pd.Series,
    amount: pd.Series,
    bench_close: pd.Series,
) -> dict:
    feat = compute_features(close, amount, bench_close)
    short_state, short_reasons = classify_horizon(feat, HORIZON_SHORT)
    mid_state, mid_reasons = classify_horizon(feat, HORIZON_MID)
    return {
        "short_state": short_state,
        "mid_state": mid_state,
        "short_reasons": short_reasons,
        "mid_reasons": mid_reasons,
        "features": feat,
        "available": bool(feat.get("ok")),
    }


def build_comment(row: dict) -> str:
    """兼容旧接口：把短线理由拼成一句话。"""
    reasons = row.get("short_reasons") or []
    if reasons:
        text = "；".join(reasons[:4])
        mid = row.get("mid_state")
        short = row.get("short_state")
        if mid and short and mid != short:
            text += f"；中线={mid}"
        chg = row.get("change_pct")
        if isinstance(chg, (int, float)) and chg == chg:
            text += f"；{_change_label(row)}{fmt_chg(chg)}"
        return text
    return "数据不足，暂无点评"


def build_reason_block(row: dict) -> str:
    short_rs = row.get("short_reasons") or []
    mid_rs = row.get("mid_reasons") or []
    lines = []
    if short_rs:
        lines.append("短线理由: " + "；".join(short_rs[:4]))
    if mid_rs:
        lines.append("中线理由: " + "；".join(mid_rs[:3]))
    chg = row.get("change_pct")
    if isinstance(chg, (int, float)) and chg == chg:
        lines.append(f"{_change_label(row)}: {fmt_chg(chg)}")
    return "\n".join(f"   {x}" for x in lines) if lines else "   理由: 暂无"


def _change_label(row: dict) -> str:
    as_of = row.get("as_of")
    target = row.get("target_as_of")
    source = row.get("change_source") or CHANGE_SOURCE_ADATA
    if as_of and target and as_of < target:
        return f"涨跌(截至{as_of}，非目标日{target})"
    if source == CHANGE_SOURCE_EM_SPOT:
        return "当日涨跌(东财实时补齐)"
    if as_of and target and as_of == target:
        return "当日涨跌"
    if as_of:
        return f"涨跌(截至{as_of})"
    return "涨跌"


def _em_session() -> requests.Session:
    global _EM_SESSION
    if _EM_SESSION is not None:
        return _EM_SESSION
    with _EM_SESSION_LOCK:
        if _EM_SESSION is None:
            s = requests.Session()
            s.trust_env = False  # 避开坏代理；delay 主机可直连
            s.headers.update(
                {
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                }
            )
            _EM_SESSION = s
    return _EM_SESSION


def _etf_secid(fund_code: str) -> str:
    code = str(fund_code).strip()
    market = "0" if code.startswith(("15", "16", "18")) else "1"
    return f"{market}.{code}"


def _scale_em_price(raw, prev_close: Optional[float] = None) -> Optional[float]:
    """东财 ETF 报价常见为价格×1000；用昨收对齐。无法校验或离谱时返回 None。"""
    if raw is None:
        return None
    try:
        raw_f = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(raw_f):
        return None
    candidates = [raw_f / 1000.0, raw_f / 100.0, raw_f / 10.0, raw_f]
    if prev_close is not None and prev_close > 0:
        ranked = sorted(
            candidates,
            key=lambda x: abs(x - prev_close) / prev_close,
        )
        best = ranked[0]
        if best > 0 and abs(best - prev_close) / prev_close <= _SPOT_MAX_DEV:
            return best
        return None
    # 无昨收时只接受常见量纲且落在合理区间
    v = candidates[0]
    if v is not None and math.isfinite(v) and 0.01 <= v <= 100:
        return v
    return None


def fetch_etf_spot_em(
    fund_code: str, prev_close: Optional[float] = None
) -> Optional[dict]:
    """东财 delay 实时快照，用作 adata 缺当日 K 线时的后补。"""
    url = "https://push2delay.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": _etf_secid(fund_code),
        "fields": "f43,f47,f48,f57,f58,f60,f86,f169,f170",
    }
    try:
        r = _em_session().get(url, params=params, timeout=10)
        data = (r.json() or {}).get("data") or {}
    except Exception:
        return None
    if not data:
        return None
    close = _scale_em_price(data.get("f43"), prev_close)
    if close is None or close <= 0:
        return None
    if prev_close is not None and prev_close > 0:
        if abs(close - prev_close) / prev_close > _SPOT_MAX_DEV:
            return None
    chg = data.get("f170")
    try:
        change_pct = float(chg) / 100.0 if chg is not None else None
    except (TypeError, ValueError):
        change_pct = None
    amount = finite_float(data.get("f48"))
    if amount is not None and amount < 0:
        amount = None
    timestamp = finite_float(data.get("f86"))
    try:
        china_tz = datetime.timezone(datetime.timedelta(hours=8))
        trade_date = (
            datetime.datetime.fromtimestamp(timestamp, tz=china_tz).strftime("%Y-%m-%d")
            if timestamp is not None and timestamp > 0
            else None
        )
    except (OverflowError, OSError, ValueError):
        trade_date = None
    return {
        "code": str(data.get("f57") or fund_code),
        "name": str(data.get("f58") or ""),
        "close": close,
        "change_pct": change_pct,
        "amount": amount,
        "trade_date": trade_date,
    }


def _normalize_etf_frame(df: Optional[pd.DataFrame], start_date: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=_EMPTY_COLS)
    if df is None or df.empty:
        return empty
    try:
        out = df.copy()
        if "trade_date" not in out.columns or "close" not in out.columns:
            return empty
        out["trade_date"] = out["trade_date"].astype(str).str[:10]
        out["close"] = pd.to_numeric(out["close"], errors="coerce")
        out["amount"] = (
            pd.to_numeric(out["amount"], errors="coerce")
            if "amount" in out.columns
            else pd.NA
        )
        if "change_pct" in out.columns:
            out["change_pct"] = pd.to_numeric(out["change_pct"], errors="coerce")
        else:
            out["change_pct"] = pd.NA
        out = out.dropna(subset=["close"])
        out = out.drop_duplicates(subset=["trade_date"], keep="last")
        out = out.loc[out["trade_date"] >= start_date]
        cols = [c for c in _EMPTY_COLS if c in out.columns]
        return out[cols].sort_values("trade_date").reset_index(drop=True)
    except Exception:
        return empty


def frame_last_date(frame: pd.DataFrame) -> Optional[str]:
    if frame is None or frame.empty:
        return None
    return str(frame.iloc[-1]["trade_date"])[:10]


def resolve_target_as_of(bench_frame: pd.DataFrame) -> str:
    """
    复盘对齐日：优先宽基最新交易日；若宽基本身缺今日且交易时段可取到实时，
    则用今日。休市日要求快照日期为今日且成交额>0，避免把冻价当成新交易日。
    """
    today = datetime.date.today().strftime("%Y-%m-%d")
    bench_last = frame_last_date(bench_frame) or today
    if bench_last >= today:
        return bench_last

    now = datetime.datetime.now()
    if now.weekday() >= 5 or now.hour < 9:
        return bench_last

    prev = None
    if bench_frame is not None and not bench_frame.empty:
        prev = finite_float(bench_frame.iloc[-1].get("close"))
    spot = fetch_etf_spot_em(BENCH_CODE, prev_close=prev)
    if not spot or spot.get("close") is None:
        return bench_last
    if spot.get("trade_date") != today:
        return bench_last
    amt = spot.get("amount")
    if amt is None or not (isinstance(amt, (int, float)) and amt > 0):
        return bench_last
    return today


def ensure_etf_as_of(
    frame: pd.DataFrame,
    fund_code: str,
    target_as_of: str,
) -> tuple[pd.DataFrame, dict]:
    """
    先裁掉目标日之后的数据；若最后一根 K 线仍早于目标日，
    仅用同日东财快照补齐。
    返回 (frame, meta)：as_of / change_source / patched。
    """
    if frame is not None and not frame.empty:
        dates = frame["trade_date"].astype(str).str[:10]
        frame = frame.loc[dates <= target_as_of].reset_index(drop=True)

    meta = {
        "as_of": frame_last_date(frame),
        "change_source": CHANGE_SOURCE_ADATA,
        "patched": False,
    }
    if frame is None or frame.empty:
        return frame if frame is not None else pd.DataFrame(columns=_EMPTY_COLS), meta

    last = meta["as_of"]
    if last is None or last >= target_as_of:
        return frame, meta

    prev_close = finite_float(frame.iloc[-1].get("close"))
    if prev_close is None or prev_close <= 0:
        return frame, meta

    spot = fetch_etf_spot_em(fund_code, prev_close=prev_close)
    if not spot or spot.get("close") is None:
        return frame, meta
    if spot.get("trade_date") != target_as_of:
        return frame, meta

    close = float(spot["close"])
    if close <= 0:
        return frame, meta

    change_pct = spot.get("change_pct")
    if change_pct is None:
        change_pct = (close / prev_close - 1.0) * 100.0

    # amount 缺失时沿用昨额，避免 _align_series.dropna 把补齐日整行丢掉
    amount = finite_float(spot.get("amount"))
    if amount is None or amount <= 0:
        amount = finite_float(frame.iloc[-1].get("amount"))
    if amount is None or amount <= 0:
        amount = 1.0

    patch = {
        "trade_date": target_as_of,
        "close": close,
        "amount": amount,
        "change_pct": change_pct,
    }
    out = pd.concat([frame, pd.DataFrame([patch])], ignore_index=True)
    out = out.drop_duplicates(subset=["trade_date"], keep="last")
    out = out.sort_values("trade_date").reset_index(drop=True)
    meta.update(
        {
            "as_of": target_as_of,
            "change_source": CHANGE_SOURCE_EM_SPOT,
            "patched": True,
        }
    )
    return out, meta


def fetch_etf_frame(fund_code: str, start_date: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=_EMPTY_COLS)
    try:
        df = adata.fund.market.get_market_etf(
            fund_code=str(fund_code), k_type=1, start_date=start_date
        )
        return _normalize_etf_frame(df, start_date)
    except Exception:
        return empty


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


def analyze_one(
    meta: dict,
    bench_close: pd.Series,
    start_date: str,
    target_as_of: Optional[str] = None,
) -> dict:
    code = meta["code"]
    row = {
        "code": code,
        "name": meta.get("name", ""),
        "theme": meta.get("theme", ""),
        "category": meta.get("category", ""),
        "available": False,
        "short_state": STATE_CHOP,
        "mid_state": STATE_CHOP,
        "short_reasons": ["行情不足"],
        "mid_reasons": [],
        "change_pct": None,
        "as_of": None,
        "target_as_of": target_as_of,
        "change_source": CHANGE_SOURCE_ADATA,
        "patched": False,
        "features": {},
        "comment": "行情不足",
    }
    frame = fetch_etf_frame(code, start_date)
    if frame.empty:
        return row

    if target_as_of:
        frame, asof_meta = ensure_etf_as_of(frame, code, target_as_of)
        row["as_of"] = asof_meta.get("as_of")
        row["change_source"] = asof_meta.get("change_source") or CHANGE_SOURCE_ADATA
        row["patched"] = bool(asof_meta.get("patched"))
    else:
        row["as_of"] = frame_last_date(frame)

    close, amount = _frame_ohlc_series(frame)
    result = classify_etf(close, amount, bench_close)
    row.update(
        {
            "available": result["available"],
            "short_state": result["short_state"],
            "mid_state": result["mid_state"],
            "short_reasons": result.get("short_reasons") or [],
            "mid_reasons": result.get("mid_reasons") or [],
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
        workers = env_int("OMG_ETF_WORKERS", DEFAULT_WORKERS)
    max_n = env_int("OMG_ETF_MAX", 0) or None

    universe = load_universe(universe_path)
    if max_n is not None:
        universe = sorted(universe, key=lambda u: u.get("mcap") or 0, reverse=True)[
            :max_n
        ]

    start = history_start_date()
    print(f"获取对照宽基 {BENCH_CODE} ...")
    bench_frame = fetch_etf_frame(BENCH_CODE, start)
    if bench_frame.empty:
        print("宽基行情获取失败")
        return []

    target_as_of = resolve_target_as_of(bench_frame)
    bench_frame, bench_meta = ensure_etf_as_of(bench_frame, BENCH_CODE, target_as_of)
    bench_as_of = frame_last_date(bench_frame)
    # 宽基补齐失败时回退目标日，避免单票显示「今日」而特征对齐仍用昨日
    if bench_as_of and bench_as_of < target_as_of:
        print(f"宽基未能对齐 {target_as_of}，回退目标日为 {bench_as_of}")
        target_as_of = bench_as_of
    elif bench_meta.get("patched"):
        print(f"宽基缺原目标日，已用东财实时补齐")
    print(f"对齐目标交易日 {target_as_of}")

    bench_close = _prep_series(_frame_ohlc_series(bench_frame)[0])
    if bench_close.empty:
        print("宽基行情获取失败")
        return []

    n_workers = min(workers, max(1, len(universe)))
    print(f"扫描主题 ETF（{len(universe)} 只，短线+中线）...")
    rows = []
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futs = {
            ex.submit(analyze_one, u, bench_close, start, target_as_of): u["code"]
            for u in universe
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
    patched = sum(1 for r in available if r.get("patched"))
    stale = sum(
        1
        for r in available
        if r.get("as_of") and r.get("target_as_of") and r["as_of"] < r["target_as_of"]
    )
    print(
        f"有效样本 {len(available)}/{len(rows)}；东财实时补齐 {patched}；仍滞后 {stale}"
    )
    return select_for_report(available, top_per_state=top_per_state)


def print_etf_report(rows: list[dict]) -> None:
    if not rows:
        print("无可用 ETF 状态结果")
        return

    target = next((r.get("target_as_of") for r in rows if r.get("target_as_of")), None)
    print("\n=== 主题 ETF 走势点评（短线+中线）===")
    print("说明: 宇宙=每主题规模最大1只；对照=510300 沪深300ETF。")
    print("      短线≈5-20日，中线≈20-60日；每条含判定理由（规则+关键数字）。")
    if target:
        print(f"      对齐目标日={target}；缺 K 线时用东财实时补齐，并标注来源。\n")
    else:
        print()

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
            as_of = r.get("as_of") or "-"
            print(
                f"{i}. {r['theme']}  {r['code']} {r['name']}  "
                f"短线:{r['short_state']} 中线:{r['mid_state']}  "
                f"涨跌:{fmt_chg(r.get('change_pct'))}@{as_of} 超额10日:{excess_s}"
            )
            print(build_reason_block(r))
        print()
