"""行业/主题 ETF 走势状态与规则点评（omg etf）。"""

from __future__ import annotations

import datetime
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import adata
import pandas as pd
import requests

from stocks.quote import clean_numeric, parse_date_series
from stocks.util import env_int, finite_float, fmt_chg, recent_date

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
_EMPTY_COLS = ["trade_date", "open", "high", "low", "close", "amount", "change_pct"]
_EM_SESSION: Optional[requests.Session] = None
_EM_SESSION_LOCK = threading.Lock()
CHANGE_SOURCE_ADATA = "adata"
CHANGE_SOURCE_EM_SPOT = "eastmoney_spot"
# 补齐价相对昨收允许偏离；超出则视为东财异常，放弃补齐
_SPOT_MAX_DEV = 0.25
_TZ = ZoneInfo("Asia/Shanghai")

# 短线 / 中线阈值（同一套判定流水线）。
# 经验规则 v1：调整阈值需同步更新测试与文档并升版本号。
_HORIZON_CFG_VERSION = 1
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
        mcap = finite_float(rec.get("mcap"))
        rows.append(
            {
                "code": code,
                "name": str(rec.get("name", "") or "").strip(),
                "category": str(rec.get("category", "") or "").strip(),
                "theme": str(rec.get("theme", "") or "").strip(),
                "mcap": mcap if mcap is not None and mcap >= 0 else 0.0,
            }
        )
    return rows


def history_start_date(calendar_days: int = LOOKBACK_CALENDAR_DAYS) -> str:
    return recent_date(calendar_days)


def _prep_series(s: pd.Series) -> pd.Series:
    out = clean_numeric(s)
    out.index = parse_date_series(out.index)
    out = out[~out.index.isna()]
    return out[~out.index.duplicated(keep="last")].sort_index()


def _empty_features() -> dict:
    return {
        "ret_5": None,
        "ret_10": None,
        "ret_20": None,
        "excess_10": None,
        "excess_20": None,
        "benchmark_available": False,
        "ma20": None,
        "ma60": None,
        "ma20_slope": None,
        "above_ma20": None,
        "above_ma60": None,
        "ma_bull": None,
        "ma_bear": None,
        "vol_ratio": None,
        "volume_available": False,
        "range_pos_60": None,
        "vol_20": None,
        "vol_60": None,
        "ma_spread": None,
        "close": None,
        "as_of": None,
        "ok": False,
    }


def compute_features(
    close: pd.Series,
    amount: pd.Series,
    bench_close: pd.Series,
) -> dict:
    empty = _empty_features()
    # 价格独立决定趋势特征；基准和成交额只决定各自派生项，不能因
    # 任一辅助序列末日缺失而把末日收盘价也删掉。
    c = (
        _prep_series(close)
        .replace([math.inf, -math.inf], float("nan"))
        .dropna()
    )
    c = c[c > 0]
    if len(c) < MIN_BARS:
        return empty

    b = _prep_series(bench_close).replace(
        [math.inf, -math.inf], float("nan")
    ).reindex(c.index)
    b = b.where(b > 0)
    a = _prep_series(amount).replace(
        [math.inf, -math.inf], float("nan")
    ).reindex(c.index)
    a = a.where(a > 0)

    if len(c) > FEATURE_TAIL:
        c, b = c.iloc[-FEATURE_TAIL:], b.iloc[-FEATURE_TAIL:]
        a = a.reindex(c.index)

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
        if pd.isna(b.iloc[-1]) or pd.isna(b.iloc[-1 - n]):
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
    excess_10 = last_excess(10)
    excess_20 = last_excess(20)

    return {
        "ret_5": last_ret(5),
        "ret_10": last_ret(10),
        "ret_20": last_ret(20),
        "excess_10": excess_10,
        "excess_20": excess_20,
        "benchmark_available": (
            excess_10 is not None and excess_20 is not None
        ),
        "ma20": ma20_now,
        "ma60": ma60_now,
        "ma20_slope": ma20_slope,
        "above_ma20": close_now > ma20_now,
        "above_ma60": close_now > ma60_now,
        "ma_bull": close_now > ma20_now > ma60_now,
        "ma_bear": close_now < ma20_now < ma60_now,
        "vol_ratio": vol_ratio,
        "volume_available": vol_ratio is not None,
        "range_pos_60": range_pos,
        "vol_20": vol_20,
        "vol_60": vol_60,
        "ma_spread": ma_spread,
        "close": close_now,
        "as_of": c.index[-1].strftime("%Y-%m-%d"),
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
    vol_ratio_value = finite_float(features.get("vol_ratio"))
    has_volume = vol_ratio_value is not None
    vol_ratio = vol_ratio_value if vol_ratio_value is not None else 0.0
    range_pos = _feature_number(features, "range_pos_60", 0.5)
    ma_spread = _feature_number(features, "ma_spread", 0.0)
    vol_20 = features.get("vol_20")
    vol_60 = features.get("vol_60")
    excess_value = finite_float(features.get(cfg["excess_key"]))
    has_excess = excess_value is not None
    excess = excess_value if excess_value is not None else 0.0
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
            f"{excess_name}{_pct(excess_value)}",
            f"量比(5/20日){_num(vol_ratio_value)}",
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
            ret_5 < cfg["top_ret_hard"]
            or (
                has_volume
                and ret_5 <= 0
                and vol_ratio >= cfg["top_vol"]
            )
        )
        top_detail = []
        if range_pos >= cfg["top_range"]:
            top_detail.append(f"处60日偏高位({_num(range_pos * 100, 0)}%)")
        if ret_5 < cfg["top_ret_hard"]:
            top_detail.append(f"近5日回落({_pct(ret_5)})")
        elif has_volume and ret_5 <= 0 and vol_ratio >= cfg["top_vol"]:
            top_detail.append(f"高位滞涨放量(5日{_pct(ret_5)}，量比{_num(vol_ratio)})")
    else:
        top_hit = range_pos >= cfg["top_range"] and (
            ret < cfg["top_ret_hard"]
            or (
                has_volume
                and ret <= 0.01
                and vol_ratio >= cfg["top_vol"]
            )
        )
        top_detail = []
        if range_pos >= cfg["top_range"]:
            top_detail.append(f"处60日偏高位({_num(range_pos * 100, 0)}%)")
        if ret < cfg["top_ret_hard"]:
            top_detail.append(f"{ret_name}明显回落({_pct(ret)})")
        elif has_volume and ret <= 0.01 and vol_ratio >= cfg["top_vol"]:
            top_detail.append(f"高位滞涨({ret_name}{_pct(ret)}，量比{_num(vol_ratio)})")

    if top_hit:
        return STATE_WEAKEN, [f"{label}见顶：" + "；".join(top_detail)] + metrics()[:3]

    if above_ma20 is False and range_pos >= cfg["break_range"] and slope <= 0:
        return STATE_WEAKEN, [
            f"{label}见顶：跌破MA20且均线走平/下行",
            f"60日位置仍偏高({_num(range_pos * 100, 0)}%)",
            f"MA20斜率{_pct(slope)}",
            f"{excess_name}{_pct(excess_value)}",
        ]

    if (
        above_ma20 is False
        and has_excess
        and excess < -0.04
        and range_pos >= 0.45
        and slope <= 0
        and ret < 0
    ):
        return STATE_WEAKEN, [
            f"{label}见顶：破位且显著跑输大盘",
            f"跌破MA20，{excess_name}{_pct(excess_value)}",
            f"60日位置{_num(range_pos * 100, 0)}%，{ret_name}{_pct(ret)}",
        ]

    if ma_bear and slope < 0 and ret < 0:
        return STATE_DOWN, [
            f"{label}空头：价格<MA20<MA60（空头排列）",
            f"MA20下行({_pct(slope)})，{ret_name}{_pct(ret)}",
            f"{excess_name}{_pct(excess_value)}，量比{_num(vol_ratio_value)}",
        ]

    start_ok = (
        above_ma20
        and slope > cfg["start_slope"]
        and has_excess
        and excess > cfg["start_excess"]
        and has_volume
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
            f"{excess_name}{_pct(excess_value)}，量比{_num(vol_ratio)}",
            f"60日位置{_num(range_pos * 100, 0)}%（尚未极端高位）",
        ]

    if cfg["strong_need_excess"]:
        if ma_bull and slope >= 0 and has_excess and excess >= 0:
            return STATE_STRONG, [
                f"{label}强趋势：多头排列(价>MA20>MA60)且未跑输大盘",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess_value)}",
                f"60日位置{_num(range_pos * 100, 0)}%，量比{_num(vol_ratio_value)}",
            ]
        if (
            above_ma20
            and slope > 0
            and cfg["strong_alt_ret"] is not None
            and ret > cfg["strong_alt_ret"]
        ):
            return STATE_STRONG, [
                f"{label}强趋势：站上MA20且{ret_name}涨幅较大({_pct(ret)})",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess_value)}",
            ]
    else:
        if ma_bull and slope >= 0:
            return STATE_STRONG, [
                f"{label}强趋势：多头排列且均线未下行",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess_value)}",
                f"60日位置{_num(range_pos * 100, 0)}%",
            ]
        if (
            cfg["strong_use_ma60"]
            and features.get("above_ma60")
            and slope > 0
            and has_excess
            and excess > 0
        ):
            return STATE_STRONG, [
                f"{label}强趋势：站上MA60 + 均线向上 + 超额为正",
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess_value)}",
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
                f"{excess_name}{_pct(excess_value)}",
            ]
    else:
        if ma_bear or (not above_ma20 and ret < cfg["down_alt_ret"]):
            why = [f"{label}空头："]
            if ma_bear:
                why[0] += "空头排列"
            else:
                why[0] += f"跌破MA20且{ret_name}弱势({_pct(ret)})"
            return STATE_DOWN, why + [
                f"MA20斜率{_pct(slope)}，{excess_name}{_pct(excess_value)}",
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
        if finite_float(chg) is not None:
            text += f"；{_change_label(row)}{fmt_chg(chg)}"
        session_path = row.get("session_path") or {}
        path = session_path.get("path_label")
        if path:
            text += f"；日内{path}"
        elif session_path.get("path_status") in ("ohl_missing", "ohl_inconsistent"):
            text += f"；日内路径缺口({session_path['path_status']})"
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
    if finite_float(chg) is not None:
        lines.append(f"{_change_label(row)}: {fmt_chg(chg)}")
    path = row.get("session_path") or {}
    label = path.get("path_label")
    if label:
        bits = [f"日内路径: {label}"]
        if path.get("open_pct") is not None:
            bits.append(f"开盘{path['open_pct']:+.2f}%")
        if path.get("close_vs_open") is not None:
            bits.append(f"较开盘{path['close_vs_open']:+.2f}%")
        lines.append("，".join(bits))
    elif path.get("path_status") in ("ohl_missing", "ohl_inconsistent"):
        lines.append(f"日内路径: 数据缺口({path['path_status']})")
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
    """东财 ETF 报价常见为价格×1000；用昨收对齐。无法校验或离谱时返回 None。

    无昨收分支依赖「东财恒为 ×1000」假设且只收第一个候选量纲——这是脆弱假设；
    但当前实际调用路径（resolve_target_as_of / ensure_etf_as_of）都会传入 prev_close，
    该分支极少触发，仅在极端缺昨收时兜底，不做多候选启发式以免引入误判。
    """
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


def session_path_from_bar(
    *,
    open_: Optional[float],
    high: Optional[float],
    low: Optional[float],
    close: Optional[float],
    prev_close: Optional[float],
) -> dict:
    """
    用当日 OHLC 相对昨收刻画日内路径（高开低走/冲高回落等）。
    不替代完整分时；缺 OHL 时 path_status=ohl_missing。
    """
    empty = {
        "open_pct": None,
        "close_vs_open": None,
        "upper_wick_pct": None,
        "lower_wick_pct": None,
        "path_label": None,
        "path_status": "ohl_missing",
    }
    o = finite_float(open_)
    h = finite_float(high)
    lo = finite_float(low)
    c = finite_float(close)
    prev = finite_float(prev_close)
    if o is None or h is None or lo is None or c is None or prev is None or prev <= 0:
        return empty
    if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
        return empty
    # 轻度脏数据容忍：高低与开收矛盾时仍算，但标签降为缺口
    if h < max(o, c) or lo > min(o, c):
        return {**empty, "path_status": "ohl_inconsistent"}

    open_pct = round((o / prev - 1.0) * 100, 2)
    close_vs_open = round((c / o - 1.0) * 100, 2)
    upper_wick_pct = round((h - max(o, c)) / prev * 100, 2)
    lower_wick_pct = round((min(o, c) - lo) / prev * 100, 2)
    off_high_pct = round((h - c) / prev * 100, 2)
    off_low_pct = round((c - lo) / prev * 100, 2)

    label = "震荡"
    if open_pct >= 1.0 and close_vs_open <= -1.0:
        label = "高开低走"
    elif open_pct <= -1.0 and close_vs_open >= 1.0:
        label = "低开高走"
    elif upper_wick_pct >= 1.0 and off_high_pct >= 1.0:
        label = "冲高回落"
    elif lower_wick_pct >= 1.0 and off_low_pct >= 1.0:
        label = "探底回升"
    elif off_high_pct <= 0.35 and (c / prev - 1.0) * 100 >= 0.5:
        label = "收在高位"
    elif off_low_pct <= 0.35 and (c / prev - 1.0) * 100 <= -0.5:
        label = "收在低位"

    return {
        "open_pct": open_pct,
        "close_vs_open": close_vs_open,
        "upper_wick_pct": upper_wick_pct,
        "lower_wick_pct": lower_wick_pct,
        "path_label": label,
        "path_status": "ok",
    }


def fetch_etf_spot_em(
    fund_code: str, prev_close: Optional[float] = None
) -> Optional[dict]:
    """东财 delay 实时快照，用作 adata 缺当日 K 线时的后补。"""
    url = "https://push2delay.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": _etf_secid(fund_code),
        # f43最新 f44最高 f45最低 f46今开 f47量 f48额 f57码 f58名 f60昨收 f86时间 f170涨跌幅
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f169,f170",
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
    open_ = _scale_em_price(data.get("f46"), prev_close)
    high = _scale_em_price(data.get("f44"), prev_close)
    low = _scale_em_price(data.get("f45"), prev_close)
    chg = finite_float(data.get("f170"))
    change_pct = chg / 100.0 if chg is not None else None
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
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "prev_close": _scale_em_price(data.get("f60"), prev_close),
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
        parsed_dates = parse_date_series(out["trade_date"])
        out["close"] = clean_numeric(out["close"])
        for col in ("open", "high", "low"):
            if col in out.columns:
                out[col] = clean_numeric(out[col])
            else:
                out[col] = pd.NA
        out["amount"] = (
            clean_numeric(out["amount"])
            if "amount" in out.columns
            else pd.NA
        )
        if "change_pct" in out.columns:
            out["change_pct"] = clean_numeric(out["change_pct"])
        else:
            out["change_pct"] = pd.NA
        out = out.replace([math.inf, -math.inf], pd.NA)
        valid = parsed_dates.notna() & (out["close"] > 0)
        out = out.loc[valid].copy()
        if out.empty:
            return empty
        out["trade_date"] = parsed_dates.loc[valid].dt.strftime("%Y-%m-%d")
        out["amount"] = out["amount"].where(out["amount"] > 0)
        for col in ("open", "high", "low"):
            out[col] = out[col].where(out[col] > 0)
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
    now = datetime.datetime.now(_TZ)
    today = now.date().isoformat()
    bench_last = frame_last_date(bench_frame) or today
    if bench_last >= today:
        return bench_last

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


def _is_partial_spot_day(
    target_as_of: str,
    *,
    now: Optional[datetime.datetime] = None,
) -> bool:
    """目标日尚未收盘时，实时成交额只是半日累计，不能与完整日比较。"""
    current = now or datetime.datetime.now(_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TZ)
    else:
        current = current.astimezone(_TZ)
    return (
        str(target_as_of)[:10] == current.date().isoformat()
        and current.time() < datetime.time(15, 0)
    )


def _bar_missing_ohl(row) -> bool:
    return any(finite_float(row.get(col)) is None for col in ("open", "high", "low"))


def ensure_etf_as_of(
    frame: pd.DataFrame,
    fund_code: str,
    target_as_of: str,
) -> tuple[pd.DataFrame, dict]:
    """
    先裁掉目标日之后的数据；若最后一根 K 线仍早于目标日，
    仅用同日东财快照补齐。目标日已有 K 线但缺 OHL 时，只回填开高低。
    返回 (frame, meta)：as_of / change_source / patched / ohl_patched。
    """
    if frame is not None and not frame.empty:
        dates = frame["trade_date"].astype(str).str[:10]
        frame = frame.loc[dates <= target_as_of].reset_index(drop=True)

    historical_amount = (
        finite_float(frame.iloc[-1].get("amount"))
        if frame is not None and not frame.empty
        else None
    )
    meta = {
        "as_of": frame_last_date(frame),
        "change_source": CHANGE_SOURCE_ADATA,
        "patched": False,
        "ohl_patched": False,
        "volume_status": (
            "historical"
            if historical_amount is not None and historical_amount > 0
            else "missing"
        ),
    }
    if frame is None or frame.empty:
        return frame if frame is not None else pd.DataFrame(columns=_EMPTY_COLS), meta

    last = meta["as_of"]
    if last is None or last > target_as_of:
        return frame, meta

    if last == target_as_of:
        frame = frame.copy()
        if _is_partial_spot_day(target_as_of):
            frame.loc[frame.index[-1], "amount"] = pd.NA
            meta["volume_status"] = "partial_session"
        if not _bar_missing_ohl(frame.iloc[-1]):
            return frame, meta
        # 目标日已有收盘行但缺 OHL：用东财快照只补开高低
        prev_close = (
            finite_float(frame.iloc[-2].get("close"))
            if len(frame) >= 2
            else None
        )
        spot = fetch_etf_spot_em(fund_code, prev_close=prev_close)
        if (
            not spot
            or spot.get("trade_date") != target_as_of
        ):
            return frame, meta
        # 只允许把同一时点的 OHL 与现有收盘价组合；否则会拼出不存在的 K 线。
        row_close = finite_float(frame.iloc[-1].get("close"))
        spot_close = finite_float(spot.get("close"))
        if (
            row_close is None
            or spot_close is None
            or not math.isclose(row_close, spot_close, rel_tol=1e-6, abs_tol=5e-4)
        ):
            return frame, meta
        filled = False
        for col in ("open", "high", "low"):
            if finite_float(frame.iloc[-1].get(col)) is None:
                val = finite_float(spot.get(col))
                if val is not None and val > 0:
                    frame.loc[frame.index[-1], col] = val
                    filled = True
        if filled:
            meta["ohl_patched"] = True
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

    change_pct = finite_float(spot.get("change_pct"))
    if change_pct is None:
        change_pct = (close / prev_close - 1.0) * 100.0

    amount = finite_float(spot.get("amount"))
    if _is_partial_spot_day(target_as_of):
        # 盘中累计额与历史完整日不可比；保留价格，但显式禁用量比。
        amount = None
        volume_status = "partial_session"
    elif amount is None or amount <= 0:
        amount = None
        volume_status = "missing"
    else:
        volume_status = "complete"

    patch = {
        "trade_date": target_as_of,
        "open": finite_float(spot.get("open")),
        "high": finite_float(spot.get("high")),
        "low": finite_float(spot.get("low")),
        "close": close,
        "amount": amount,
        "change_pct": change_pct,
    }
    out = frame.copy()
    new_index = len(out)
    out = out.reindex(range(new_index + 1))
    for column, value in patch.items():
        out.at[new_index, column] = value
    out = out.drop_duplicates(subset=["trade_date"], keep="last")
    out = out.sort_values("trade_date").reset_index(drop=True)
    meta.update(
        {
            "as_of": target_as_of,
            "change_source": CHANGE_SOURCE_EM_SPOT,
            "patched": True,
            "volume_status": volume_status,
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
    indexed.index = pd.to_datetime(indexed.index, errors="coerce")
    indexed = indexed[~indexed.index.isna()]
    close = pd.to_numeric(indexed["close"], errors="coerce").sort_index()
    amount = (
        pd.to_numeric(indexed["amount"], errors="coerce").sort_index()
        if "amount" in indexed.columns
        else pd.Series(dtype=float)
    )
    return close, amount


def _parse_change_pct(value) -> Optional[float]:
    return finite_float(value)


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
        "ohl_patched": False,
        "volume_status": "historical",
        "features": {},
        "session_path": {},
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
        row["ohl_patched"] = bool(asof_meta.get("ohl_patched"))
        row["volume_status"] = asof_meta.get("volume_status") or "missing"
    else:
        row["as_of"] = frame_last_date(frame)
        latest_amount = finite_float(frame.iloc[-1].get("amount"))
        row["volume_status"] = (
            "historical"
            if latest_amount is not None and latest_amount > 0
            else "missing"
        )

    close, amount = _frame_ohlc_series(frame)
    result = classify_etf(close, amount, bench_close)
    last = frame.iloc[-1]
    prev_close = None
    if len(frame) >= 2:
        prev_close = finite_float(frame.iloc[-2].get("close"))
    session_path = session_path_from_bar(
        open_=finite_float(last.get("open")),
        high=finite_float(last.get("high")),
        low=finite_float(last.get("low")),
        close=finite_float(last.get("close")),
        prev_close=prev_close,
    )
    row.update(
        {
            "available": result["available"],
            "short_state": result["short_state"],
            "mid_state": result["mid_state"],
            "short_reasons": result.get("short_reasons") or [],
            "mid_reasons": result.get("mid_reasons") or [],
            "change_pct": _parse_change_pct(last.get("change_pct")),
            "features": result["features"],
            "session_path": session_path,
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
    *,
    for_report: bool = True,
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
    elif bench_meta.get("ohl_patched"):
        print("宽基目标日 OHL 缺失，已用东财快照回填")
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
    ohl_patched = sum(1 for r in available if r.get("ohl_patched"))
    stale = sum(
        1
        for r in available
        if r.get("as_of") and r.get("target_as_of") and r["as_of"] < r["target_as_of"]
    )
    print(
        f"有效样本 {len(available)}/{len(rows)}；东财实时补齐 {patched}；"
        f"OHL 回填 {ohl_patched}；仍滞后 {stale}"
    )
    if for_report:
        return select_for_report(available, top_per_state=top_per_state)
    return available


def print_etf_report(rows: list[dict]) -> None:
    if not rows:
        print("无可用 ETF 状态结果")
        return

    target = next((r.get("target_as_of") for r in rows if r.get("target_as_of")), None)
    print("\n=== 主题 ETF 走势点评（短线+中线）===")
    print("说明: 宇宙=静态清单中每主题1只代表ETF；对照=510300 沪深300ETF。")
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
