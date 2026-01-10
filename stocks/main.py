import stocks.watch_list as watch_list
import stocks.db as db
import stocks.ana as ana
import stocks.market as market


def my_list():
    # 大盘指数配置
    indexes = [
        ("000001", "上证指数"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
    ]

    print("=== 大盘指数 ===")
    for code, name in indexes:
        index_data = market.get_index_current_price(code, name)
        if index_data:
            print(index_data)

    print("\n=== 自选股票 ===")
    watch_list.get_watch_list_current_price()


def notice():
    watch_list.get_notice_list_current_price()


def hot():
    ana.concept_strength_top10()
    # ana.hot_concept_ths()
