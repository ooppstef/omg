import logging
from datetime import date, datetime, timedelta
from threading import RLock

import adata
import pandas as pd

from stocks.stock_models import StockCurrentPrice
from stocks.util import finite_float


_INDEX_LOOKBACK_DAYS = 15
_prev_close_cache = {}
_cache_lock = RLock()


def get_market_current_price(stock_codes):
    if not stock_codes:
        return []
    res_df = adata.stock.market.list_market_current(stock_codes)
    if res_df is None or res_df.empty:
        return []
    rows = []
    for _, row in res_df.iterrows():
        price = finite_float(row.get("price"))
        if price is None or price <= 0:
            continue
        amount = finite_float(row.get("amount"))
        trade_date = _parse_trade_date(row.get("trade_date"))
        rows.append(
            StockCurrentPrice(
                row.get("stock_code"),
                row.get("short_name"),
                price,
                finite_float(row.get("change_pct")),
                amount if amount is not None and amount >= 0 else None,
                trade_date.isoformat() if trade_date is not None else None,
            )
        )
    return rows


def _parse_trade_date(trade_date):
    try:
        if pd.isna(trade_date):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(trade_date, date) and not isinstance(trade_date, datetime):
        return trade_date
    if hasattr(trade_date, "date"):
        parsed = trade_date.date()
        return parsed if isinstance(parsed, date) else None
    if trade_date is not None:
        text = str(trade_date).strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _remember_previous_close(index_code, trade_date_key, previous_close):
    with _cache_lock:
        _prev_close_cache[(index_code, trade_date_key)] = previous_close


def _previous_close(index_code, trade_date):
    parsed_trade_date = _parse_trade_date(trade_date)
    if parsed_trade_date is None:
        return None
    trade_date_key = parsed_trade_date.strftime("%Y-%m-%d")
    cache_key = (index_code, trade_date_key)
    with _cache_lock:
        cached = _prev_close_cache.get(cache_key)
    if cached is not None:
        return cached

    start_date = (
        parsed_trade_date - timedelta(days=_INDEX_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")
    try:
        hist_df = adata.stock.market.ths_index.get_market_index(
            index_code=index_code, start_date=start_date
        )
    except Exception as exc:
        logging.debug("指数 %s 历史行情获取失败: %s", index_code, exc)
        return None

    if hist_df is None or isinstance(hist_df, Exception) or hist_df.empty:
        return None
    if not {"trade_date", "close"}.issubset(hist_df.columns):
        return None

    hist_df = hist_df.copy()
    hist_df["_parsed_trade_date"] = pd.to_datetime(
        hist_df["trade_date"], errors="coerce"
    )
    hist_df = hist_df.dropna(subset=["_parsed_trade_date"]).sort_values(
        by="_parsed_trade_date"
    )
    target_date = pd.Timestamp(parsed_trade_date)
    prev_rows = hist_df[hist_df["_parsed_trade_date"] < target_date]
    if prev_rows.empty:
        return None
    candidate_row = prev_rows.iloc[-1]

    previous_close = finite_float(candidate_row.get("close"))
    if previous_close is None or previous_close <= 0:
        return None

    _remember_previous_close(index_code, trade_date_key, previous_close)
    return previous_close


def get_index_current_price(index_code, index_name):
    """
    获取单个指数的实时行情

    参数:
        index_code: 指数代码（如 "000001"）
        index_name: 指数名称（如 "上证指数"）

    返回:
        StockCurrentPrice 对象，如果获取失败返回 None
    """
    try:
        res_df = adata.stock.market.ths_index.get_market_index_current(index_code)
        if res_df is None or res_df.empty:
            return None

        row = res_df.iloc[0]
        price = finite_float(row.get("price"))
        if price is None or price <= 0:
            return None
        trade_date = _parse_trade_date(row.get("trade_date"))
        previous_close = _previous_close(index_code, trade_date)

        if previous_close is not None and previous_close > 0:
            change_pct = ((price - previous_close) / previous_close) * 100
        else:
            # 开盘到当前的涨跌并不是日涨跌；历史昨收缺失时宁可显式留空。
            change_pct = finite_float(row.get("change_pct"))

        amount = finite_float(row.get("amount"))
        return StockCurrentPrice(
            index_code,
            index_name,
            price,
            round(change_pct, 2) if change_pct is not None else None,
            amount if amount is not None and amount >= 0 else None,
            trade_date.isoformat() if trade_date is not None else None,
        )
    except Exception as e:
        print(f"获取指数 {index_code} 失败: {e}")
        return None
