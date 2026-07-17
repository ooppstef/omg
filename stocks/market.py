from datetime import datetime, timedelta
import logging
from threading import RLock

import adata
from stocks.stock_models import StockCurrentPrice


_INDEX_LOOKBACK_DAYS = 15
_prev_close_cache = {}
_last_prev_close = {}
_cache_lock = RLock()


def get_market_current_price(stock_codes):
    res_df = adata.stock.market.list_market_current(stock_codes)
    stock_current_prices = []
    for _, row in res_df.iterrows():
        stock_current_prices.append(
            StockCurrentPrice(
                row["stock_code"],
                row["short_name"],
                row["price"],
                row["change_pct"],
                row["amount"],
            )
        )
    return stock_current_prices


def _parse_trade_date(trade_date):
    if hasattr(trade_date, "date"):
        return trade_date.date()
    if isinstance(trade_date, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(trade_date, fmt).date()
            except ValueError:
                continue
    return datetime.today().date()


def _remember_previous_close(index_code, trade_date_key, previous_close):
    with _cache_lock:
        _prev_close_cache[(index_code, trade_date_key)] = previous_close
        _last_prev_close[index_code] = previous_close


def _get_last_prev_close(index_code):
    with _cache_lock:
        return _last_prev_close.get(index_code)


def _previous_close(index_code, trade_date):
    parsed_trade_date = _parse_trade_date(trade_date)
    trade_date_key = parsed_trade_date.strftime("%Y-%m-%d")
    cache_key = (index_code, trade_date_key)
    with _cache_lock:
        cached = _prev_close_cache.get(cache_key)
    if cached is not None:
        return cached

    start_date = (parsed_trade_date - timedelta(days=_INDEX_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        hist_df = adata.stock.market.ths_index.get_market_index(
            index_code=index_code, start_date=start_date
        )
    except Exception as exc:
        logging.debug("指数 %s 历史行情获取失败: %s", index_code, exc)
        return _get_last_prev_close(index_code)

    if hist_df is None or isinstance(hist_df, Exception) or hist_df.empty:
        return _get_last_prev_close(index_code)

    hist_df = hist_df.sort_values(by="trade_date")
    prev_rows = hist_df[hist_df["trade_date"] < trade_date_key]
    candidate_row = prev_rows.iloc[-1] if not prev_rows.empty else hist_df.iloc[-1]

    try:
        previous_close = float(candidate_row["close"])
    except (TypeError, ValueError):
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
        price = float(row["price"])
        previous_close = _previous_close(index_code, row["trade_date"])

        if previous_close and previous_close != 0:
            change_pct = ((price - previous_close) / previous_close) * 100
        else:
            open_price = float(row["open"])
            change_pct = ((price - open_price) / open_price) * 100 if open_price else 0.0

        return StockCurrentPrice(
            index_code,
            index_name,
            price,
            round(change_pct, 2),
            row["amount"],
        )
    except Exception as e:
        print(f"获取指数 {index_code} 失败: {e}")
        return None
