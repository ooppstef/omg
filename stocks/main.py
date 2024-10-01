import adata
import stocks.watch_list as watch_list
import stocks.db as db


def go():
    watch_list.get_watch_list_current_price()
    # watch_list.get_notice_list_current_price()
