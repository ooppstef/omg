import adata
import datetime
from stocks.stock_models import StockCurrentPrice


def get_recent_date(n):
    """
    获取n天前的日期，格式为 'YYYY-MM-DD'

    参数:
    n (int): 要获取的天数

    返回:
    str: n天前的日期，格式为 'YYYY-MM-DD'
    """
    today = datetime.date.today()
    target_date = today - datetime.timedelta(days=n)
    return target_date.strftime("%Y-%m-%d")


def ths_top_list(top=30):
    # 东方财富网-数据中心-热门数据-热门数据排行.其实更希望用同花顺的,但库报错.
    res_df = adata.sentiment.hot.pop_rank_100_east()
    stock_current_prices = []
    for index, row in res_df.iterrows():
        stock_current_prices.append(
            StockCurrentPrice(
                row["stock_code"],
                row["short_name"],
                row["price"],
                row["change_pct"],
                "0",
            )
        )
    stock_current_prices = stock_current_prices[:top]
    for stock in stock_current_prices:
        print(stock)
    return stock_current_prices
