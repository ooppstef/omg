"""市场情绪：涨停四池 → 阶段判定 + 近 N 日趋势（omg mood）。"""

from __future__ import annotations

import datetime
import math
from datetime import date, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from stocks.util import env_int, finite_float, force_direct_http

STATE_ICE = "冰点"
STATE_REPAIR = "修复"
STATE_MAIN = "主升"
STATE_DIVERGE = "分歧"
STATE_EBB = "退潮"
STATE_NEUTRAL = "中性"

STATES = (
    STATE_ICE,
    STATE_REPAIR,
    STATE_MAIN,
    STATE_DIVERGE,
    STATE_EBB,
    STATE_NEUTRAL,
)

DEFAULT_DAYS = 8
_TZ = ZoneInfo("Asia/Shanghai")

# 情绪阶段判定的经验阈值（v1；调整阈值需同步更新测试与文档，并升版本号）
_MOOD_RULES = {
    "version": 1,
    # 退潮：亏钱效应主导
    "ebb_limit_down_min": 30,      # 跌停 ≥30
    "ebb_limit_up_max": 30,        # 涨停 <30 且炸板率高
    "ebb_high_zhaban_rate": 0.50,  # 炸板率 >50%
    "ebb_premium_min": -5.0,       # 昨涨停今日均溢价 ≤-5%
    # 冰点：涨停稀少
    "ice_limit_up_max": 30,
    # 分歧：承接犹豫
    "diverge_high_up": 60,         # 涨停 ≥60 但高度 <5 且跌停 ≥15
    "diverge_high_height": 5,
    "diverge_high_down": 15,
    "diverge_mid_down_low": 10,    # 10 ≤ 跌停 <30 且高度 ≥3 且涨停 <60
    "diverge_mid_down_high": 30,
    "diverge_mid_height": 3,
    "diverge_mid_up": 60,
    "diverge_rate_up": 30,         # 涨停 ≥30 且炸板率在 30%..50%
    "diverge_rate_low": 0.30,
    "diverge_rate_high": 0.50,
    # 主升：赚钱效应全面
    "main_up_min": 60,
    "main_height_min": 5,
    "main_zhaban_rate_max": 0.30,
    "main_down_max": 10,
    # 修复：升温但未高潮
    "repair_up_low": 30,
    "repair_up_high": 60,
    "repair_down_max": 10,
    "repair_rate_max": 0.35,
    "repair_height_low": 2,
    "repair_height_high": 4,
}


def zhaban_rate(limit_up: int, zhaban: int) -> float:
    denom = limit_up + zhaban
    if denom <= 0:
        return 0.0
    return zhaban / denom


def _as_records(df: Optional[pd.DataFrame]) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def _num(row: dict, *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in row:
            value = finite_float(row[key])
            if value is not None:
                return value
    return default


def _int(row: dict, *keys: str, default: int = 0) -> int:
    return int(_num(row, *keys, default=float(default)))


def _board_count(row: dict, *keys: str) -> Optional[int]:
    for key in keys:
        value = finite_float(row.get(key))
        if value is not None and value >= 1 and value.is_integer():
            return int(value)
    return None


def compute_mood_metrics(
    zt_rows: list[dict],
    zb_rows: list[dict],
    dt_rows: list[dict],
    prev_rows: list[dict],
) -> dict[str, Any]:
    limit_up = len(zt_rows)
    zhaban = len(zb_rows)
    limit_down = len(dt_rows)
    board_counts: list[int] = []
    first_boards = 0
    for row in zt_rows:
        boards = _board_count(row, "连板数", "昨日连板数")
        if boards is None:
            continue
        board_counts.append(boards)
        if boards == 1:
            first_boards += 1

    premiums: list[float] = []
    for row in prev_rows:
        # 昨日涨停池接口的「涨跌幅」= 今日相对表现，即隔日溢价
        if "涨跌幅" in row and row["涨跌幅"] is not None:
            try:
                value = float(row["涨跌幅"])
                if math.isfinite(value):
                    premiums.append(value)
            except (TypeError, ValueError):
                pass
    premium = sum(premiums) / len(premiums) if premiums else None
    board_count_coverage = (
        len(board_counts) / limit_up if limit_up else 0.0
    )
    board_stats_available = bool(limit_up) and len(board_counts) == limit_up
    height = max(board_counts) if board_stats_available else None
    first_board_ratio = (
        first_boards / len(board_counts) if board_stats_available else None
    )
    rate = zhaban_rate(limit_up, zhaban)

    # 至少要有当日涨停/跌停/炸板之一，避免「仅昨日池」误判
    ok = limit_up > 0 or limit_down > 0 or zhaban > 0
    return {
        "ok": ok,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "zhaban": zhaban,
        "zhaban_rate": rate,
        "height": height,
        "premium": premium,
        "first_board_ratio": first_board_ratio,
        "board_count_coverage": board_count_coverage,
        "prev_count": len(prev_rows),
    }


def classify_mood(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    if not metrics or not metrics.get("ok"):
        return STATE_NEUTRAL, ["情绪数据不足，不作强弱判定"]

    up = int(metrics.get("limit_up") or 0)
    down = int(metrics.get("limit_down") or 0)
    zb = int(metrics.get("zhaban") or 0)
    rate = float(metrics.get("zhaban_rate") or 0.0)
    height_value = finite_float(metrics.get("height"))
    height = int(height_value) if height_value is not None else None
    premium = metrics.get("premium")
    first_ratio = finite_float(metrics.get("first_board_ratio"))

    def base_why() -> list[str]:
        lines = [
            f"涨停{up}家 / 跌停{down}家 / 炸板{zb}家",
            f"炸板率{rate * 100:.0f}%（炸板/(涨停+炸板)）",
            f"连板高度{height}板" if height is not None else "连板高度数据不足",
        ]
        if premium is not None:
            lines.append(f"昨日涨停今日均溢价{premium:+.1f}%")
        lines.append(
            f"首板占比{first_ratio * 100:.0f}%"
            if first_ratio is not None
            else "首板占比数据不足"
        )
        return lines

    r = _MOOD_RULES
    # 1) 退潮：亏钱效应主导
    if (
        down >= r["ebb_limit_down_min"]
        or (up < r["ebb_limit_up_max"] and rate > r["ebb_high_zhaban_rate"])
        or (premium is not None and premium <= r["ebb_premium_min"])
    ):
        why = ["退潮：亏钱效应显著"]
        if down >= r["ebb_limit_down_min"]:
            why.append(f"跌停偏多({down}家)")
        if up < r["ebb_limit_up_max"] and rate > r["ebb_high_zhaban_rate"]:
            why.append(f"涨停偏少({up})且炸板率高({rate * 100:.0f}%)")
        if premium is not None and premium <= r["ebb_premium_min"]:
            why.append(f"昨涨停溢价极差({premium:+.1f}%)")
        return STATE_EBB, why + base_why()[:3]

    # 2) 冰点：涨停稀少（须在「仅炸板率分歧」之前，避免冷市被标分歧）
    if up < r["ice_limit_up_max"]:
        why = [
            "冰点：涨停稀少，进攻意愿弱",
            (
                f"涨停仅{up}家，高度{height}板"
                if height is not None
                else f"涨停仅{up}家，连板高度数据不足"
            ),
            f"跌停{down}家，炸板率{rate * 100:.0f}%",
        ]
        return STATE_ICE, why

    # 3) 分歧：承接犹豫（炸板率分歧仅在涨停已达一定热度时启用）
    high_up_weak_height = (
        height is not None
        and up >= r["diverge_high_up"]
        and height < r["diverge_high_height"]
        and down >= r["diverge_high_down"]
    )
    mid_diverge = (
        height is not None
        and r["diverge_mid_down_low"] <= down < r["diverge_mid_down_high"]
        and height >= r["diverge_mid_height"]
        and up < r["diverge_mid_up"]
    )
    rate_diverge = (
        up >= r["diverge_rate_up"]
        and r["diverge_rate_low"] <= rate <= r["diverge_rate_high"]
    )
    if rate_diverge or mid_diverge or high_up_weak_height:
        why = ["分歧：资金承接犹豫"]
        if rate_diverge:
            why.append(f"炸板率处分歧区({rate * 100:.0f}%)")
        if high_up_weak_height:
            why.append(f"涨停虽多({up})但高度不足({height}板)且跌停抬升({down})")
        elif mid_diverge:
            why.append(f"跌停抬升({down}家)且高度仍在({height}板)")
        return STATE_DIVERGE, why + base_why()[:3]

    # 4) 主升：赚钱效应全面
    if (
        height is not None
        and up >= r["main_up_min"]
        and height >= r["main_height_min"]
        and rate < r["main_zhaban_rate_max"]
        and down < r["main_down_max"]
    ):
        why = [
            "主升：涨停多、高度够、炸板低、跌停少",
            f"涨停{up}家，高度{height}板，炸板率{rate * 100:.0f}%",
        ]
        if premium is not None:
            why.append(f"昨涨停溢价{premium:+.1f}%")
        return STATE_MAIN, why + base_why()[3:]

    # 5) 修复：升温但未高潮
    if (
        height is not None
        and r["repair_up_low"] <= up <= r["repair_up_high"]
        and down < r["repair_down_max"]
        and rate < r["repair_rate_max"]
        and r["repair_height_low"] <= height <= r["repair_height_high"]
    ):
        why = [
            "修复：情绪回暖但未到高潮",
            f"涨停{up}家，高度{height}板，跌停{down}家",
            f"炸板率{rate * 100:.0f}%",
        ]
        return STATE_REPAIR, why

    return STATE_NEUTRAL, ["中性：指标未落入明确阶段"] + base_why()


def _import_ak():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "omg mood 需要 akshare，请先 pip install akshare"
        ) from exc
    return ak


def _recent_calendar_dates(n: int) -> list[str]:
    """多取日历日，后续用有数据的交易日截取。"""
    today = datetime.datetime.now(_TZ).date()
    return [
        (today - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(n * 3 + 5)
    ]


def fetch_pools_for_date(trade_date: str) -> dict[str, Any]:
    ak = _import_ak()
    result: dict[str, Any] = {
        "date": trade_date,
        "zt": [],
        "zb": [],
        "dt": [],
        "prev": [],
        "errors": [],
    }
    fetchers = {
        "zt": ak.stock_zt_pool_em,
        "zb": ak.stock_zt_pool_zbgc_em,
        "dt": ak.stock_zt_pool_dtgc_em,
        "prev": ak.stock_zt_pool_previous_em,
    }
    with force_direct_http():
        for key, fn in fetchers.items():
            try:
                df = fn(date=trade_date)
                result[key] = _as_records(df)
            except Exception as exc:
                result[key] = []
                result["errors"].append(f"{key}:{type(exc).__name__}")
    return result


def fetch_mood_history(days: Optional[int] = None) -> list[dict[str, Any]]:
    """拉取近 N 个有涨停池数据的交易日。"""
    n = days if days is not None else env_int("OMG_MOOD_DAYS", DEFAULT_DAYS)
    candidates = _recent_calendar_dates(n)
    rows: list[dict[str, Any]] = []
    error_days = 0

    # 串行按日拉取，避免东财限流；最多扫 candidates
    for d in candidates:
        if len(rows) >= n:
            break
        pools = fetch_pools_for_date(d)
        critical_errors = [
            error
            for error in pools.get("errors", [])
            if error.split(":", 1)[0] in {"zt", "zb", "dt"}
        ]
        if critical_errors:
            error_days += 1
            continue
        if pools.get("errors") and not (
            pools["zt"] or pools["zb"] or pools["dt"] or pools["prev"]
        ):
            error_days += 1
            continue
        # 四池全空视为非交易日/无数据
        if not (pools["zt"] or pools["zb"] or pools["dt"] or pools["prev"]):
            continue
        metrics = compute_mood_metrics(
            pools["zt"], pools["zb"], pools["dt"], pools["prev"]
        )
        if not metrics.get("ok"):
            continue
        state, reasons = classify_mood(metrics)
        rows.append(
            {
                "trade_date": date.fromisoformat(
                    f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                ).isoformat(),
                "metrics": metrics,
                "state": state,
                "reasons": reasons,
                "pools": pools,
            }
        )

    rows.sort(key=lambda r: r["trade_date"])
    if error_days and len(rows) < n:
        print(f"警告: {error_days} 个交易日四池请求失败，仅得到 {len(rows)}/{n} 日")
    elif len(rows) < n:
        print(f"提示: 仅得到 {len(rows)}/{n} 个有效交易日情绪样本")
    return rows


def print_mood_report(history: list[dict[str, Any]]) -> None:
    if not history:
        print("无可用情绪数据（akshare 涨停池为空或网络失败）")
        return

    latest = history[-1]
    m = latest["metrics"]
    print("\n=== 市场情绪（omg mood）===")
    print(
        "说明: 数据=akshare 东财涨停/炸板/跌停/昨日涨停池；"
        "炸板率=炸板/(涨停+炸板)；溢价=昨日涨停股今日均涨跌。\n"
    )
    print(f"【结论】{latest['state']}  （{latest['trade_date']}）")
    for i, reason in enumerate(latest["reasons"][:5], 1):
        print(f"  {i}. {reason}")

    print("\n--- 当日指标 ---")
    premium = m.get("premium")
    premium_s = f"{premium:+.2f}%" if premium is not None else "-"
    height_s = str(m["height"]) if m.get("height") is not None else "-"
    first_ratio = finite_float(m.get("first_board_ratio"))
    first_ratio_s = (
        f"{first_ratio * 100:.0f}%" if first_ratio is not None else "-"
    )
    print(
        f"涨停 {m['limit_up']}  跌停 {m['limit_down']}  炸板 {m['zhaban']}  "
        f"炸板率 {m['zhaban_rate'] * 100:.1f}%  高度 {height_s}  "
        f"首板占比 {first_ratio_s}  昨涨停溢价 {premium_s}"
    )

    # 连板梯队摘要
    zt = latest.get("pools", {}).get("zt") or []
    ladder: dict[int, int] = {}
    for row in zt:
        b = _board_count(row, "连板数")
        if b is None:
            continue
        ladder[b] = ladder.get(b, 0) + 1
    if ladder:
        parts = [f"{k}板×{ladder[k]}" for k in sorted(ladder, reverse=True)]
        print("连板梯队: " + "  ".join(parts[:8]))

    print(f"\n--- 近 {len(history)} 日趋势 ---")
    header = f"{'日期':<10} {'阶段':<6} {'涨停':>4} {'跌停':>4} {'炸板':>4} {'炸板率':>7} {'高度':>4} {'溢价':>8}"
    print(header)
    print("-" * len(header))
    for row in history:
        mm = row["metrics"]
        prem = mm.get("premium")
        prem_s = f"{prem:+.1f}%" if prem is not None else "-"
        height_s = str(mm["height"]) if mm.get("height") is not None else "-"
        print(
            f"{row['trade_date']:<10} {row['state']:<6} "
            f"{mm['limit_up']:>4} {mm['limit_down']:>4} {mm['zhaban']:>4} "
            f"{mm['zhaban_rate'] * 100:>6.1f}% {height_s:>4} {prem_s:>8}"
        )
    print()


def analyze(days: Optional[int] = None) -> list[dict[str, Any]]:
    print("拉取涨停四池（akshare）...")
    history = fetch_mood_history(days=days)
    print_mood_report(history)
    return history
