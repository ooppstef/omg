"""市场情绪：涨停四池 → 阶段判定 + 近 N 日趋势（omg mood）。"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

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


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


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
        if key in row and row[key] is not None and row[key] == row[key]:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return default


def _int(row: dict, *keys: str, default: int = 0) -> int:
    return int(_num(row, *keys, default=float(default)))


def compute_mood_metrics(
    zt_rows: list[dict],
    zb_rows: list[dict],
    dt_rows: list[dict],
    prev_rows: list[dict],
) -> dict[str, Any]:
    limit_up = len(zt_rows)
    zhaban = len(zb_rows)
    limit_down = len(dt_rows)
    height = 0
    first_boards = 0
    for row in zt_rows:
        boards = _int(row, "连板数", "昨日连板数")
        height = max(height, boards)
        if boards <= 1:
            first_boards += 1

    premiums: list[float] = []
    for row in prev_rows:
        # 昨日涨停池接口的「涨跌幅」= 今日相对表现，即隔日溢价
        if "涨跌幅" in row and row["涨跌幅"] is not None:
            try:
                premiums.append(float(row["涨跌幅"]))
            except (TypeError, ValueError):
                pass
    premium = sum(premiums) / len(premiums) if premiums else None
    first_board_ratio = (first_boards / limit_up) if limit_up else 0.0
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
        "prev_count": len(prev_rows),
    }


def classify_mood(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    if not metrics or not metrics.get("ok"):
        return STATE_NEUTRAL, ["情绪数据不足，不作强弱判定"]

    up = int(metrics.get("limit_up") or 0)
    down = int(metrics.get("limit_down") or 0)
    zb = int(metrics.get("zhaban") or 0)
    rate = float(metrics.get("zhaban_rate") or 0.0)
    height = int(metrics.get("height") or 0)
    premium = metrics.get("premium")
    first_ratio = float(metrics.get("first_board_ratio") or 0.0)

    def base_why() -> list[str]:
        lines = [
            f"涨停{up}家 / 跌停{down}家 / 炸板{zb}家",
            f"炸板率{rate * 100:.0f}%（炸板/(涨停+炸板)）",
            f"连板高度{height}板",
        ]
        if premium is not None:
            lines.append(f"昨日涨停今日均溢价{premium:+.1f}%")
        lines.append(f"首板占比{first_ratio * 100:.0f}%")
        return lines

    # 1) 退潮：亏钱效应主导
    if down >= 30 or (up < 30 and rate > 0.50) or (
        premium is not None and premium <= -5.0
    ):
        why = ["退潮：亏钱效应显著"]
        if down >= 30:
            why.append(f"跌停偏多({down}家)")
        if up < 30 and rate > 0.50:
            why.append(f"涨停偏少({up})且炸板率高({rate * 100:.0f}%)")
        if premium is not None and premium <= -5.0:
            why.append(f"昨涨停溢价极差({premium:+.1f}%)")
        return STATE_EBB, why + base_why()[:3]

    # 2) 冰点：涨停稀少（须在「仅炸板率分歧」之前，避免冷市被标分歧）
    if up < 30:
        why = [
            "冰点：涨停稀少，进攻意愿弱",
            f"涨停仅{up}家，高度{height}板",
            f"跌停{down}家，炸板率{rate * 100:.0f}%",
        ]
        return STATE_ICE, why

    # 3) 分歧：承接犹豫（炸板率分歧仅在涨停已达一定热度时启用）
    high_up_weak_height = up >= 60 and height < 5 and down >= 15
    mid_diverge = 10 <= down < 30 and height >= 3 and up < 60
    rate_diverge = up >= 30 and 0.30 <= rate <= 0.50
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
    if up >= 60 and height >= 5 and rate < 0.30 and down < 10:
        why = [
            "主升：涨停多、高度够、炸板低、跌停少",
            f"涨停{up}家，高度{height}板，炸板率{rate * 100:.0f}%",
        ]
        if premium is not None:
            why.append(f"昨涨停溢价{premium:+.1f}%")
        return STATE_MAIN, why + base_why()[3:]

    # 5) 修复：升温但未高潮
    if 30 <= up < 60 and down < 10 and rate < 0.35 and 2 <= height <= 4:
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


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _recent_calendar_dates(n: int) -> list[str]:
    """多取日历日，后续用有数据的交易日截取。"""
    today = date.today()
    out: list[str] = []
    for i in range(n * 3 + 5):
        out.append(_yyyymmdd(today - timedelta(days=i)))
    return out


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
    n = days if days is not None else _env_int("OMG_MOOD_DAYS", DEFAULT_DAYS)
    candidates = _recent_calendar_dates(n)
    rows: list[dict[str, Any]] = []
    error_days = 0

    # 串行按日拉取，避免东财限流；最多扫 candidates
    for d in candidates:
        if len(rows) >= n:
            break
        pools = fetch_pools_for_date(d)
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
                "trade_date": d,
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
    print(
        f"涨停 {m['limit_up']}  跌停 {m['limit_down']}  炸板 {m['zhaban']}  "
        f"炸板率 {m['zhaban_rate'] * 100:.1f}%  高度 {m['height']}  "
        f"首板占比 {m['first_board_ratio'] * 100:.0f}%  昨涨停溢价 {premium_s}"
    )

    # 连板梯队摘要
    zt = latest.get("pools", {}).get("zt") or []
    ladder: dict[int, int] = {}
    for row in zt:
        b = _int(row, "连板数")
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
        print(
            f"{row['trade_date']:<10} {row['state']:<6} "
            f"{mm['limit_up']:>4} {mm['limit_down']:>4} {mm['zhaban']:>4} "
            f"{mm['zhaban_rate'] * 100:>6.1f}% {mm['height']:>4} {prem_s:>8}"
        )
    print()


def analyze(days: Optional[int] = None) -> list[dict[str, Any]]:
    print("拉取涨停四池（akshare）...")
    history = fetch_mood_history(days=days)
    print_mood_report(history)
    return history
