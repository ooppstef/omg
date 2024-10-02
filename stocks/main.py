import adata
import stocks.watch_list as watch_list
import stocks.db as db
import stocks.ana as ana


def my_list():
    watch_list.get_watch_list_current_price()


def notice():
    watch_list.get_notice_list_current_price()


def hot():
    ana.ths_top_list()
