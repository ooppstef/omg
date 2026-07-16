import argparse
import re


COMMANDS = ("list", "hot", "notice", "rot", "filter", "copy", "crowd", "etf")


def run_command(command):
    if command == "copy":
        import stocks.copy_watchlist as copy_watchlist

        copy_watchlist.copy_watchlist()
        return

    import stocks.main as stocks

    actions = {
        "list": stocks.my_list,
        "notice": stocks.notice,
        "hot": stocks.hot,
        "rot": stocks.rot,
        "filter": stocks.filter_menu,
        "crowd": stocks.crowd,
        "etf": stocks.etf,
    }
    actions[command]()


def main():
    parser = argparse.ArgumentParser(description="基本命令")
    parser.add_argument(
        "command",
        nargs="?",
        choices=COMMANDS,
        help="执行的命令: list(自选列表), hot(热点强度), notice(公告列表), rot(板块轮动), filter(筛选菜单), copy(同步自选), crowd(交易拥挤度), etf(主题ETF点评)",
    )
    parser.add_argument(
        "-k",
        type=str,
        nargs="+",
        metavar="概念",
        help="绘制概念板块强度曲线，支持多个板块对比",
    )
    args = parser.parse_args()

    if args.k:
        import stocks.main as stocks

        combined = " ".join(args.k)
        concepts = [c.strip() for c in re.split(r"[,，\s]+", combined) if c.strip()]
        stocks.k(concepts)
        return

    if not args.command:
        print("用法: omg [list|hot|notice|rot|filter|copy|crowd|etf] 或 omg -k 概念名称")
        return

    run_command(args.command)


if __name__ == "__main__":
    main()
