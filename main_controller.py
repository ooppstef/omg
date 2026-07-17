import argparse
import re
import sys


COMMAND_META = {
    "init": ("init_data", "初始化本地数据"),
    "list": ("my_list", "自选列表"),
    "hot": ("hot", "热点强度"),
    "notice": ("notice", "公告列表"),
    "rot": ("rot", "板块轮动"),
    "filter": ("filter_menu", "筛选菜单"),
    "copy": (None, "同步自选"),
    "crowd": ("crowd", "交易拥挤度"),
    "etf": ("etf", "主题ETF点评"),
    "mood": ("mood", "市场情绪"),
    "style": ("style", "风格结构"),
    "chat": (None, "AI多轮对话"),
}
COMMANDS = tuple(COMMAND_META)


def run_command(command):
    if command == "copy":
        import stocks.copy_watchlist as copy_watchlist

        return copy_watchlist.copy_watchlist()

    import stocks.main as stocks

    handler_name = COMMAND_META[command][0]
    return getattr(stocks, handler_name)()


def _run_chat(argv):
    parser = argparse.ArgumentParser(prog="omg chat", description="AI 多轮对话")
    parser.add_argument(
        "--research",
        action="store_true",
        help="research 模式（多步取证）",
    )
    parser.add_argument("prompt", nargs="*", help="首问（可选）")
    research = False
    prompt = []
    parse_options = True
    for token in argv:
        if parse_options and token == "--":
            parse_options = False
        elif parse_options and token == "--research":
            research = True
        elif parse_options and token in ("-h", "--help"):
            parser.parse_args(["--help"])
        elif parse_options and token.startswith("-"):
            parser.error(f"unrecognized arguments: {token}")
        else:
            prompt.append(token)

    from stocks.ai.chat import run as chat_run

    first = " ".join(prompt).strip() or None
    return chat_run(prompt=first, research=research)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        return _run_chat(sys.argv[2:])

    parser = argparse.ArgumentParser(description="基本命令")
    parser.add_argument(
        "command",
        nargs="?",
        choices=COMMANDS,
        help="执行的命令: "
        + ", ".join(f"{command}({meta[1]})" for command, meta in COMMAND_META.items()),
    )
    parser.add_argument(
        "-k",
        type=str,
        nargs="+",
        metavar="概念",
        help="绘制概念板块强度曲线，支持多个板块对比",
    )
    args = parser.parse_args()

    if not args.k and not args.command:
        print(f"用法: omg [{'|'.join(COMMANDS)}] 或 omg -k 概念名称")
        return 2

    if args.command == "chat":
        return _run_chat([])

    if args.k:
        import stocks.main as stocks

        combined = " ".join(args.k)
        concepts = [c.strip() for c in re.split(r"[,，\s]+", combined) if c.strip()]
        return 0 if stocks.k(concepts) is not False else 1

    return 0 if run_command(args.command) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
