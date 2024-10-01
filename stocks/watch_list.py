import re
from enum import Enum
import stocks.db as db
import stocks.list_parser as list_parser
import stocks.market as market


class ListType(Enum):
    WATCH = "自选"
    NOTICE = "查看"


def get_stock_list(list_type: ListType):
    data = list_parser.get_section_by_name(list_type.value)

    if list_type == ListType.WATCH:
        return db.search_base_by_names(data)
    elif list_type == ListType.NOTICE:
        return _process_notice_list(data)


def _process_notice_list(list_data):
    stocks = []
    pattern_alpha = re.compile(r"^[A-Za-z]+$")
    pattern_numeric = re.compile(r"^\d+$")

    for item in list_data:
        if pattern_alpha.match(item):
            stock = db.search_base_by_short_prefix(item)
        elif pattern_numeric.match(item):
            stock = [db.search_base_by_code(item)]
        else:
            stock = db.search_base_by_names_like(item)
        stocks.extend(stock if isinstance(stock, list) else [stock])

    return [s for s in stocks if s is not None]


def get_stock_list_current_price(list_type: ListType, print_result=True):
    stocks = get_stock_list(list_type)
    stock_codes = [stock.code for stock in stocks]
    data_list = market.get_market_current_price(stock_codes)
    if print_result:
        for item in data_list:
            print(item)
    return data_list


def get_watch_list_current_price(print_result=True):
    return get_stock_list_current_price(ListType.WATCH, print_result)


def get_notice_list_current_price(print_result=True):
    return get_stock_list_current_price(ListType.NOTICE, print_result)
