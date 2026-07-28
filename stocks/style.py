"""风格结构：大小盘 / 科技 / 红利相对强弱（omg style）。"""

from __future__ import annotations

import math
from typing import Any, Optional

import adata
import pandas as pd

from stocks.util import env_int, recent_date

STATE_TECH = "科技虹吸"
STATE_SMALL = "小盘占优"
STATE_LARGE = "大盘占优"
STATE_DIVIDEND = "红利防守"
STATE_BALANCED = "均衡扩散"

STATES = (
    STATE_TECH,
    STATE_SMALL,
    STATE_LARGE,
    STATE_DIVIDEND,
    STATE_BALANCED,
)

# 指数代码（adata.get_market_index）
INDEX_META = (
    ("hs300", "000300", "沪深300"),
    ("zz1000", "000852", "中证1000"),
    ("kc50", "000688", "科创50"),
    ("dividend", "000015", "红利指数"),
)

DEFAULT_DAYS = 8
LOOKBACK_CALENDAR = 120


def _pct(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{v * 100:+.2f}%"


def _ret(series: pd.Series, n: int) -> Optional[float]:
    s = (
        pd.to_numeric(series, errors="coerce")
        .replace([float("inf"), float("-inf")], pd.NA)
        .dropna()
    )
    s = s[s > 0]
    if len(s) < n + 1:
        return None
    a, b = float(s.iloc[-(n + 1)]), float(s.iloc[-1])
    if a == 0:
        return None
    return b / a - 1.0


def compute_style_features(closes: dict[str, pd.Series]) -> dict[str, Any]:
    needed = ("hs300", "zz1000", "kc50", "dividend")
    if any(k not in closes or closes[k] is None or closes[k].empty for k in needed):
        return {"ok": False}

    # 对齐日期
    frame = pd.DataFrame({k: closes[k] for k in needed})
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame[~frame.index.isna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([float("inf"), float("-inf")], pd.NA).dropna(
        how="any"
    )
    frame = frame[(frame > 0).all(axis=1)]
    if len(frame) < 25:
        return {"ok": False}

    feat: dict[str, Any] = {"ok": True}
    for key in needed:
        feat[f"ret5_{key}"] = _ret(frame[key], 5)
        feat[f"ret20_{key}"] = _ret(frame[key], 20)
        feat[f"ret60_{key}"] = _ret(frame[key], 60)

    def excess(a: str, b: str, n: int) -> Optional[float]:
        ra, rb = feat.get(f"ret{n}_{a}"), feat.get(f"ret{n}_{b}")
        if ra is None or rb is None:
            return None
        return ra - rb

    feat["excess5_kc_hs"] = excess("kc50", "hs300", 5)
    feat["excess20_kc_hs"] = excess("kc50", "hs300", 20)
    feat["excess5_zz_hs"] = excess("zz1000", "hs300", 5)
    feat["excess20_zz_hs"] = excess("zz1000", "hs300", 20)
    feat["excess5_div_hs"] = excess("dividend", "hs300", 5)
    feat["excess20_div_hs"] = excess("dividend", "hs300", 20)

    core = [
        feat["excess5_kc_hs"],
        feat["excess20_kc_hs"],
        feat["excess5_zz_hs"],
        feat["excess20_zz_hs"],
        feat["excess5_div_hs"],
        feat["excess20_div_hs"],
    ]
    if any(v is None for v in core):
        feat["ok"] = False
        return feat

    feat["frame"] = frame
    return feat


def _req(feat: dict[str, Any], key: str) -> float:
    value = feat.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise KeyError(key)
    if not math.isfinite(number):
        raise KeyError(key)
    return number


def classify_style(feat: dict[str, Any]) -> tuple[str, list[str]]:
    if not feat or not feat.get("ok"):
        return STATE_BALANCED, ["风格数据不足，无法判定（非均衡）"]

    try:
        e5_kc = _req(feat, "excess5_kc_hs")
        e20_kc = _req(feat, "excess20_kc_hs")
        e5_zz = _req(feat, "excess5_zz_hs")
        e20_zz = _req(feat, "excess20_zz_hs")
        e5_div = _req(feat, "excess5_div_hs")
        e20_div = _req(feat, "excess20_div_hs")
        ret5_kc = _req(feat, "ret5_kc50")
        ret5_div = _req(feat, "ret5_dividend")
        ret20_kc = _req(feat, "ret20_kc50")
    except (KeyError, TypeError, ValueError):
        return STATE_BALANCED, ["风格数据不足，无法判定（非均衡）"]

    metrics = [
        f"科创相对沪深300：5日{_pct(e5_kc)} / 20日{_pct(e20_kc)}",
        f"中证1000相对沪深300：5日{_pct(e5_zz)} / 20日{_pct(e20_zz)}",
        f"红利相对沪深300：5日{_pct(e5_div)} / 20日{_pct(e20_div)}",
    ]

    # 短线切防守、中线科技仍强：优先解释「当下」并标注中线
    if e5_div >= 0.02 and e5_kc <= -0.03 and e20_kc >= 0.05:
        return STATE_DIVIDEND, [
            "红利防守（短线）：资金从科技切向红利",
            f"科创5日超额{_pct(e5_kc)}，红利5日超额{_pct(e5_div)}",
            f"注意：科创20日超额仍达{_pct(e20_kc)}，中线主线未确认破坏",
        ]

    # 科技虹吸：短中期均显著跑赢大盘，且强于红利
    if (
        e5_kc >= 0.03
        and e20_kc >= 0.05
        and e5_kc > e5_div + 0.02
        and e20_kc > e20_div + 0.03
        and ret5_kc > ret5_div
    ):
        return STATE_TECH, [
            "科技虹吸：科创50短中期显著跑赢沪深300与红利",
            f"科创5日{_pct(ret5_kc)}，红利5日{_pct(ret5_div)}",
            f"科创20日{_pct(ret20_kc)}，超额20日{_pct(e20_kc)}",
        ] + metrics[:1]

    # 红利防守：红利相对占优，且短线明显强于科技/小盘
    if (
        e5_div >= 0.02
        and e5_div >= e5_kc + 0.03
        and e5_div >= e5_zz + 0.02
        and (e5_kc <= 0.01 or ret5_kc < 0)
        and (e20_div >= 0.015 or e5_div >= 0.04)
    ):
        return STATE_DIVIDEND, [
            "红利防守：红利相对占优，成长风格偏弱",
            f"红利超额5日{_pct(e5_div)} / 20日{_pct(e20_div)}",
            f"科创超额5日{_pct(e5_kc)}，小盘超额5日{_pct(e5_zz)}",
        ]

    # 小盘占优：中证1000 相对大盘优势清晰，且不弱于科创主导叙事
    if e5_zz >= 0.03 and e20_zz >= 0.05 and e20_zz >= e20_kc - 0.02:
        return STATE_SMALL, [
            "小盘占优：中证1000相对沪深300优势明确",
            f"小盘超额5日{_pct(e5_zz)} / 20日{_pct(e20_zz)}",
            f"科创超额20日{_pct(e20_kc)}（未形成更强虹吸）",
        ]

    # 大盘占优：小盘与科创均显著跑输
    if e5_zz <= -0.03 and e20_zz <= -0.04 and e5_kc <= -0.02 and e20_kc <= -0.03:
        return STATE_LARGE, [
            "大盘占优：沪深300相对中小盘/科创更强",
            f"小盘超额5日{_pct(e5_zz)} / 20日{_pct(e20_zz)}",
            f"科创超额5日{_pct(e5_kc)} / 20日{_pct(e20_kc)}",
        ]

    # 均衡：各风格之间的两两超额都不大
    spreads_5 = (0.0, e5_kc, e5_zz, e5_div)
    spreads_20 = (0.0, e20_kc, e20_zz, e20_div)
    max_pairwise = max(
        max(spreads_5) - min(spreads_5),
        max(spreads_20) - min(spreads_20),
    )
    if max_pairwise <= 0.025:
        return STATE_BALANCED, [
            "均衡扩散：风格超额幅度有限，未形成单边虹吸",
            f"最大两两超额约{_pct(max_pairwise)}",
        ] + metrics

    # 其余：短线权重更高，避免中线惯性盖住当下切换
    scores = {
        STATE_TECH: 1.2 * e5_kc + 0.4 * e20_kc,
        STATE_SMALL: 1.2 * e5_zz + 0.4 * e20_zz,
        STATE_DIVIDEND: 1.2 * e5_div + 0.4 * e20_div,
        STATE_LARGE: -0.6 * (e5_zz + e5_kc) - 0.2 * (e20_zz + e20_kc),
    }
    best = max(scores, key=scores.get)
    return best, [
        f"{best}（边界情形，短线加权择近）",
        f"得分: 科技{scores[STATE_TECH] * 100:+.1f} "
        f"小盘{scores[STATE_SMALL] * 100:+.1f} "
        f"红利{scores[STATE_DIVIDEND] * 100:+.1f} "
        f"大盘{scores[STATE_LARGE] * 100:+.1f}",
    ] + metrics


def fetch_index_close(index_code: str, start_date: str) -> pd.Series:
    df = adata.stock.market.get_market_index(
        index_code=index_code, k_type=1, start_date=start_date
    )
    if df is None or df.empty:
        return pd.Series(dtype=float)
    frame = df.copy()
    date_col = "trade_date" if "trade_date" in frame.columns else "trade_time"
    frame[date_col] = frame[date_col].astype(str).str[:10]
    close = pd.to_numeric(frame["close"], errors="coerce")
    close = close.where(close.map(lambda value: math.isfinite(value) and value > 0))
    dates = pd.to_datetime(frame[date_col], errors="coerce")
    series = pd.Series(close.values, index=dates)
    series = series[~series.index.isna()]
    return series[~series.index.duplicated(keep="last")].sort_index().dropna()


def fetch_style_closes(calendar_days: int = LOOKBACK_CALENDAR) -> dict[str, pd.Series]:
    start = recent_date(calendar_days)
    out: dict[str, pd.Series] = {}
    for key, code, _name in INDEX_META:
        try:
            out[key] = fetch_index_close(code, start)
        except Exception:
            out[key] = pd.Series(dtype=float)
    return out


def build_trend_rows(frame: pd.DataFrame, days: int) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    rets = frame.pct_change().dropna(how="all")
    tail = rets.tail(days)
    rows = []
    for ts, row in tail.iterrows():
        rows.append(
            {
                "trade_date": ts.strftime("%Y-%m-%d"),
                "hs300": float(row["hs300"]) if pd.notna(row.get("hs300")) else None,
                "zz1000": float(row["zz1000"]) if pd.notna(row.get("zz1000")) else None,
                "kc50": float(row["kc50"]) if pd.notna(row.get("kc50")) else None,
                "dividend": float(row["dividend"])
                if pd.notna(row.get("dividend"))
                else None,
            }
        )
    return rows


def print_style_report(
    feat: dict[str, Any],
    state: str,
    reasons: list[str],
    trend: list[dict[str, Any]],
) -> None:
    print("\n=== 风格结构（omg style）===")
    print(
        "说明: 数据=adata 指数日线；对照=沪深300；"
        "样本=中证1000(小盘)/科创50(科技)/红利指数(防守)。\n"
    )
    if not feat.get("ok"):
        print("【结论】数据不足")
        for r in reasons:
            print(f"  - {r}")
        return

    print(f"【结论】{state}")
    for i, reason in enumerate(reasons[:5], 1):
        print(f"  {i}. {reason}")

    print("\n--- 区间表现 ---")
    header = f"{'指数':<10} {'5日':>10} {'20日':>10} {'60日':>10} {'相对300(5日)':>14} {'相对300(20日)':>14}"
    print(header)
    print("-" * len(header))
    rows_spec = [
        ("沪深300", "hs300", None, None),
        ("中证1000", "zz1000", "excess5_zz_hs", "excess20_zz_hs"),
        ("科创50", "kc50", "excess5_kc_hs", "excess20_kc_hs"),
        ("红利指数", "dividend", "excess5_div_hs", "excess20_div_hs"),
    ]
    for name, key, e5k, e20k in rows_spec:
        e5 = _pct(feat.get(e5k)) if e5k else "-"
        e20 = _pct(feat.get(e20k)) if e20k else "-"
        print(
            f"{name:<10} {_pct(feat.get(f'ret5_{key}')):>10} "
            f"{_pct(feat.get(f'ret20_{key}')):>10} "
            f"{_pct(feat.get(f'ret60_{key}')):>10} "
            f"{e5:>14} {e20:>14}"
        )

    if trend:
        print(f"\n--- 近 {len(trend)} 日涨跌 ---")
        th = f"{'日期':<12} {'沪深300':>9} {'中证1000':>9} {'科创50':>9} {'红利':>9} {'1000-300':>9} {'科创-300':>9}"
        print(th)
        print("-" * len(th))
        for row in trend:
            hs = row["hs300"]
            zz = row["zz1000"]
            kc = row["kc50"]
            dv = row["dividend"]

            def fmt(v):
                return f"{v * 100:+.2f}%" if v is not None else "-"

            spread_zz = (
                fmt(zz - hs) if zz is not None and hs is not None else "-"
            )
            spread_kc = (
                fmt(kc - hs) if kc is not None and hs is not None else "-"
            )
            print(
                f"{row['trade_date']:<12} {fmt(hs):>9} {fmt(zz):>9} "
                f"{fmt(kc):>9} {fmt(dv):>9} {spread_zz:>9} {spread_kc:>9}"
            )
    print()


def analyze(days: Optional[int] = None) -> dict[str, Any]:
    n = days if days is not None else env_int("OMG_STYLE_DAYS", DEFAULT_DAYS)
    print("获取风格指数（adata）...")
    closes = fetch_style_closes()
    for key, code, name in INDEX_META:
        series = closes.get(key)
        status = f"{len(series)}日" if series is not None and not series.empty else "失败"
        print(f"  {name}({code}): {status}")

    feat = compute_style_features(closes)
    state, reasons = classify_style(feat)
    frame = feat.get("frame")
    trend = build_trend_rows(frame, n) if isinstance(frame, pd.DataFrame) else []
    as_of = (
        frame.index[-1].strftime("%Y-%m-%d")
        if isinstance(frame, pd.DataFrame) and not frame.empty
        else None
    )
    print_style_report(feat, state, reasons, trend)
    return {
        "as_of": as_of,
        "features": feat,
        "state": state,
        "reasons": reasons,
        "trend": trend,
    }
