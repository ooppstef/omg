import argparse
import stocks.main as stocks
import stocks.ana as ana


def main():
    parser = argparse.ArgumentParser(description="基本命令")
    parser.add_argument(
        "command", nargs="?", choices=["list", "hot", "notice"], help="执行的命令"
    )
    parser.add_argument(
        "-k", type=str, metavar="概念", help="绘制概念板块强度曲线"
    )
    args = parser.parse_args()
    
    if args.k:
        stocks.k(args.k)
    elif args.command == "list":
        stocks.my_list()
    elif args.command == "notice":
        stocks.notice()
    elif args.command == "hot":
        stocks.hot()
    else:
        print("用法: omg [list|hot|notice] 或 omg -k 概念名称")


if __name__ == "__main__":
    main()
