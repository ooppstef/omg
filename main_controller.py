import argparse
import stocks.main as stocks
import stocks.ana as ana


def main():
    parser = argparse.ArgumentParser(description="基本命令")
    parser.add_argument(
        "command", nargs="?", choices=["list", "hot", "notice", "rot"], help="执行的命令: list(自选列表), hot(热点强度), notice(公告列表), rot(板块轮动)"
    )
    parser.add_argument(
        "-k", type=str, nargs='+', metavar="概念", help="绘制概念板块强度曲线，支持多个板块对比"
    )
    args = parser.parse_args()
    
    if args.k:
        # 合并参数并按多种分隔符拆分
        import re
        combined = ' '.join(args.k)
        concepts = [c.strip() for c in re.split(r'[,，\s]+', combined) if c.strip()]
        stocks.k(concepts)
    elif args.command == "list":
        stocks.my_list()
    elif args.command == "notice":
        stocks.notice()
    elif args.command == "hot":
        stocks.hot()
    elif args.command == "rot":
        stocks.rot()
    else:
        print("用法: omg [list|hot|notice|rot] 或 omg -k 概念名称")


if __name__ == "__main__":
    main()
