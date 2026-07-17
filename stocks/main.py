from concurrent.futures import ThreadPoolExecutor
import stocks.watch_list as watch_list
import stocks.db as db
import stocks.ana as ana
import stocks.market as market
import stocks.rot as rot_ana
import stocks.crowd as crowd_ana
import stocks.etf as etf_ana
import stocks.mood as mood_ana
import stocks.style as style_ana
import stocks.filter.entry as stock_filter
from stocks.filter.algo import STRATEGIES


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
    watch_results = watch_list.get_watch_list_current_price()
    return bool(any(results) or watch_results)


def notice():
    watch_list.get_notice_list_current_price()
    return True


def hot():
    return bool(ana.concept_strength_top10())


def k(concept_names):
    """绘制概念板块强度曲线"""
    if isinstance(concept_names, str):
        concept_names = [concept_names]
    return bool(ana.concept_strength_chart(concept_names))


def rot():
    """热点板块轮动分析"""
    return bool(rot_ana.analyze(days=5, top_concepts=10, top_stocks=5))


def crowd():
    """概念交易拥挤度（3年分位）"""
    rows = crowd_ana.scan_concept_crowding(top=15)
    crowd_ana.print_crowding_table(rows)
    return bool(rows)


def etf():
    """主题 ETF 走势点评（短线+中线）"""
    rows = etf_ana.scan_etf_states()
    etf_ana.print_etf_report(rows)
    return bool(rows)


def mood():
    """市场情绪阶段（涨停四池）"""
    return bool(mood_ana.analyze())


def style():
    """风格结构（大小盘/科技/红利）"""
    result = style_ana.analyze()
    return bool(result.get("features", {}).get("ok"))


def init_data():
    """初始化用户自选文件与股票基础数据。"""
    import stocks.list_parser as list_parser

    list_parser.parse_my_list()
    print("更新股票基础数据...")
    count = db.refresh_base_data()
    print(f"初始化完成：基础股票 {count} 只")
    return True


def _read_key():
    """读取单个按键，支持 ESC 检测。"""
    import sys

    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()[:1]
    if sys.platform == "win32":
        import msvcrt

        return msvcrt.getwch()

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def filter_menu():
    """交互式筛选菜单"""
    print("\n=== OMG 筛选 ===")
    for key, (name, _) in STRATEGIES.items():
        print(f"{key}. {name}")
    print("\n请选择 (按 ESC 退出): ", end="", flush=True)

    ch = _read_key()
    print()

    if ch == "\x1b":
        print("已退出")
        return True

    if ch in STRATEGIES:
        return stock_filter.run_strategy(ch)
    else:
        print("无效选择")
        return False
