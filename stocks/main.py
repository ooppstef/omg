from concurrent.futures import ThreadPoolExecutor
import stocks.watch_list as watch_list
import stocks.db as db
import stocks.ana as ana
import stocks.market as market
import stocks.rot as rot_ana


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
    rot_ana.analyze(days=5, top_concepts=10, top_stocks=5)


def _read_key():
    """读取单个按键，支持 ESC 检测。"""
    import sys, tty, termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def filter_menu():
    """交互式菜单"""
    print("\n=== OMG 菜单 ===")
    print("1. 功能1")
    print("2. 功能2")
    print("3. 功能3")
    print("4. 功能4")
    print("\n请选择 (按 ESC 退出): ", end="", flush=True)

    ch = _read_key()
    print()  # 换行

    if ch == "\x1b":  # ESC
        print("已退出")
        return

    actions = {
        "1": lambda: print("hi1"),
        "2": lambda: print("hi2"),
        "3": lambda: print("hi3"),
        "4": lambda: print("hi4"),
    }

    action = actions.get(ch)
    if action:
        action()
    else:
        print("无效选择")
