import adata
from stocks.stock_models import StockCurrentPrice


def get_market_current_price(stock_codes):
    res_df = adata.stock.market.list_market_current(stock_codes)
    stock_current_prices = []
    for index, row in res_df.iterrows():
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

        # 计算涨跌幅
        price = float(row["price"])
        open_price = float(row["open"])
        if open_price != 0:
            change_pct = ((price - open_price) / open_price) * 100
        else:
            change_pct = 0.0

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
