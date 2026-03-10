from concurrent.futures import ThreadPoolExecutor
import stocks.watch_list as watch_list
import stocks.db as db
import stocks.ana as ana
import stocks.market as market
import stocks.rotation_analysis as rotation_ana


def my_list():
    # 大盘指数配置
    indexes = [
        ("000001", "上证指数"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
    ]

    print("=== 大盘指数 ===")
    # 并行获取指数数据
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(
            executor.map(lambda x: market.get_index_current_price(x[0], x[1]), indexes)
        )
    for index_data in results:
        if index_data:
            print(index_data)

    print("\n=== 自选股票 ===")
    watch_list.get_watch_list_current_price()


def notice():
    watch_list.get_notice_list_current_price()


def hot():
    ana.concept_strength_top10()


def k(concept_names):
    """绘制概念板块强度曲线"""
    if isinstance(concept_names, str):
        concept_names = [concept_names]
    ana.concept_strength_chart(concept_names)


def rot():
    """热点板块轮动分析"""
    rotation_ana.analyze_hot_sectors_rotation(days=5, top_concepts=10, top_stocks=5)
