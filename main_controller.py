import argparse


def main():
    parser = argparse.ArgumentParser(description="基本命令")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["list", "hot", "notice", "rot", "filter", "copy"],
        help="执行的命令: list(自选列表), hot(热点强度), notice(公告列表), rot(板块轮动), filter(筛选菜单), copy(同步自选到DeerFlow)",
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
        # 合并参数并按多种分隔符拆分
        import re
        import stocks.main as stocks

        combined = " ".join(args.k)
        concepts = [c.strip() for c in re.split(r"[,，\s]+", combined) if c.strip()]
        stocks.k(concepts)
    elif args.command == "list":
        import stocks.main as stocks

        stocks.my_list()
    elif args.command == "notice":
        import stocks.main as stocks

        stocks.notice()
    elif args.command == "hot":
        import stocks.main as stocks

        stocks.hot()
    elif args.command == "rot":
        import stocks.main as stocks

        stocks.rot()
    elif args.command == "filter":
        import stocks.main as stocks

        stocks.filter_menu()
    elif args.command == "copy":
        import stocks.copy_watchlist as copy_watchlist

        copy_watchlist.copy_watchlist()
    else:
        print("用法: omg [list|hot|notice|rot|filter|copy] 或 omg -k 概念名称")


if __name__ == "__main__":
    main()
