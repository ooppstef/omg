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
