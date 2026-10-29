"""前期强势龙头的当日分时体检：S 点 / 退潮前置信号。

收盘宽度（板块 breadth、涨停数）是滞后指标；龙头“当涨不涨、冲高回落、
触板开板”往往更早出现。本模块用问财取近 10 日涨幅前 N 的题材龙头，
再用新浪日线补当日开/高/低/收，逐只算：

- 最大涨幅（high / prev_close - 1）
- 收盘涨幅（close / prev_close - 1）
- 回落幅度（最大涨幅 - 收盘涨幅）
- 是否触及涨停后开板

输出是描述性规则标签，不是收益预测或交易指令。
"""

from __future__ import annotations

import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from stocks.util import finite_float, recent_date

DEFAULT_THEME = "机器人"
DEFAULT_TOP = 10
MAX_TOP = 20

FADE_PP = 4.0
LEADER_RET10_MIN = 30.0

_TZ = ZoneInfo("Asia/Shanghai")


def _limit_pct(code: str) -> float:
    """按板块返回涨停幅度近似阈值（不含 ST）。"""
    c = str(code).strip()
    if c.startswith(("30", "68")):
        return 20.0
    if c.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def _supported_code(code: str) -> bool:
    """新浪日线只覆盖沪深 A 股；北交所（8/4/920）跳过并记缺口。"""
    c = str(code).strip()
    return len(c) == 6 and c.isdigit() and not c.startswith(("4", "8", "9"))


def _ret10_of(row: dict) -> Optional[float]:
    for key, value in row.items():
        if "区间涨跌幅:前复权[" in str(key):
            return finite_float(value)
    return None


def _fetch_frame(code: str, *, days: int = 40) -> pd.DataFrame:
    from stocks.watch_advice import _fetch_kline_akshare

    start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    return _fetch_kline_akshare(code, start=start)


def build_item(
    code: str,
    name: str,
    ret10: Optional[float],
    frame: pd.DataFrame,
    *,
    expected_date: Optional[str] = None,
) -> dict[str, Any]:
    """把一只龙头的最新两根日线转成分时体检项；数据不足返回空 dict。"""
    if frame is None or len(frame) < 2:
        return {}
    last = frame.iloc[-1]
    prev = frame.iloc[-2]
    try:
        prev_close = float(prev["close"])
        open_ = float(last["open"])
        high = float(last["high"])
        low = float(last["low"])
        close = float(last["close"])
    except (KeyError, TypeError, ValueError):
        return {}
    if prev_close <= 0 or high <= 0 or close <= 0:
        return {}
    trade_date = str(last.get("trade_date") or "")
    if expected_date and trade_date and trade_date != expected_date:
        return {}

    open_gain = (open_ / prev_close - 1.0) * 100.0
    max_gain = (high / prev_close - 1.0) * 100.0
    close_gain = (close / prev_close - 1.0) * 100.0
    fade_pp = max_gain - close_gain
    close_loc = (close - low) / (high - low) if high > low else 0.5

    limit_pct = _limit_pct(code)
    hit_limit = max_gain >= limit_pct * 0.97
    opened_limit = hit_limit and close_gain < limit_pct * 0.97
    leader_break = (
        (ret10 or 0.0) >= LEADER_RET10_MIN
        and close_gain < 0.0
        and fade_pp >= FADE_PP
    )
    fade = max_gain >= 3.0 and fade_pp >= FADE_PP

    flags: list[str] = []
    if leader_break:
        flags.append("当涨不涨")
    if fade:
        flags.append("冲高回落")
    if opened_limit:
        flags.append("触板开板")
    if close_gain < 0:
        flags.append("收跌")

    return {
        "code": code,
        "name": name,
        "trade_date": trade_date,
        "ret10": None if ret10 is None else round(ret10, 2),
        "open_gain": round(open_gain, 2),
        "max_gain": round(max_gain, 2),
        "close_gain": round(close_gain, 2),
        "fade_pp": round(fade_pp, 2),
        "close_loc": round(close_loc, 3),
        "limit_pct": limit_pct,
        "hit_limit": hit_limit,
        "opened_limit": opened_limit,
        "leader_break": leader_break,
        "fade": fade,
        "flags": flags,
    }


def summarize(items: list[dict[str, Any]], *, theme: str, session: str) -> dict[str, Any]:
    """汇总 S 点分时信号；items 按近 10 日涨幅降序。"""
    n = len(items)
    if n == 0:
        return {
            "theme": theme,
            "n": 0,
            "score": None,
            "stage": "数据缺口",
            "reasons": ["无可用龙头分时数据"],
        }

    n_down = sum(1 for item in items if item["close_gain"] < 0)
    n_fade = sum(1 for item in items if item["fade"])
    n_limit_open = sum(1 for item in items if item["opened_limit"])
    avg_fade = sum(item["fade_pp"] for item in items) / n
    top = items[0]
    top_break = bool(top["leader_break"])
    broad_fade = (n_fade / n) >= 0.5

    score = 100.0 * (
        0.4 * (n_down / n)
        + 0.3 * (n_fade / n)
        + 0.2 * (n_limit_open / n)
        + 0.1 * (1.0 if top_break else 0.0)
    )

    if top_break and broad_fade:
        stage = "S点确认（分时）"
    elif top_break or broad_fade or score >= 40:
        stage = "S点预警"
    else:
        stage = "未触发"

    reasons = [
        (
            f"10日龙头 {top['name']}（{top['code']}）"
            f"{_fmt_pct(top['ret10'])}，今日 {_fmt_pct(top['close_gain'])}，"
            f"最高 {_fmt_pct(top['max_gain'])}，回落 {top['fade_pp']:.1f}pp"
        ),
        f"前 {n} 龙头：{n_down} 只收跌、{n_fade} 只冲高回落≥{FADE_PP:.0f}pp、{n_limit_open} 只触板开板",
        f"平均回落 {avg_fade:.1f}pp；龙头失败={'是' if top_break else '否'}，扩散失败={'是' if broad_fade else '否'}",
        f"时点：{session}；规则标签，非收益预测",
    ]
    return {
        "theme": theme,
        "n": n,
        "n_down": n_down,
        "n_fade": n_fade,
        "n_limit_open": n_limit_open,
        "avg_fade": round(avg_fade, 2),
        "top_leader": top,
        "top_leader_fail": top_break,
        "broad_fade": broad_fade,
        "score": round(score, 1),
        "stage": stage,
        "reasons": reasons,
    }


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}%"


def analyze(theme: str = DEFAULT_THEME, top: int = DEFAULT_TOP) -> dict[str, Any]:
    """取题材近 10 日龙头，逐只体检当日分时；返回描述性 S 点标签。"""
    import stocks.wencai as wencai

    theme = (theme or DEFAULT_THEME).strip()[:20] or DEFAULT_THEME
    top = max(3, min(int(top), MAX_TOP))
    today = recent_date(0)
    now = datetime.datetime.now(_TZ)

    payload = wencai.query(
        f"近10日涨幅前{top}的{theme}概念股",
        perpage=top,
    )
    rows = payload.get("rows") or []
    if not rows:
        raise RuntimeError(f"问财未返回 {theme} 龙头列表")

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows[:top]:
        raw_code = str(row.get("股票代码") or "")
        code = raw_code.split(".")[0].strip()
        name = str(row.get("股票简称") or "").strip()
        if not _supported_code(code):
            errors.append(f"{name or code}:代码不支持（北交所/非沪深 A 股）")
            continue
        ret10 = _ret10_of(row)
        try:
            frame = _fetch_frame(code)
        except Exception as exc:  # 单只失败不拖垮整组
            errors.append(f"{name or code}:日线失败 {type(exc).__name__}")
            continue
        item = build_item(code, name, ret10, frame, expected_date=today)
        if not item:
            errors.append(f"{name or code}:当日日线缺失")
            continue
        items.append(item)

    dates = {item["trade_date"] for item in items}
    as_of = max(dates) if dates else today
    if as_of != today:
        session = f"非当日（最新 {as_of}）"
    elif now.hour < 15:
        session = "盘中"
    else:
        session = "收盘"

    summary = summarize(items, theme=theme, session=session)
    return {
        "theme": theme,
        "as_of": as_of,
        "observed_at": now.isoformat(timespec="seconds"),
        "session": session,
        "top": top,
        "leaders": items,
        "summary": summary,
        "errors": errors,
        "notes": [
            "龙头 = 近 10 日涨幅前 N 的题材股，由问财现取；日线来自新浪，非逐笔分时。",
            "S点只看“龙头当涨不涨/冲高回落/触板开板”，与收盘 breadth 是两套口径；收盘宽度高不能否定分时负反馈。",
            "score / stage 为规则标签，不是概率或交易建议；触板阈值按 10/20/30cm 近似。",
        ],
    }
