import argparse
import stocks.main as stocks


def main():
    parser = argparse.ArgumentParser(description="基本命令")
    parser.add_argument("command", nargs="?", choices=["go"], help="执行的命令")
    args = parser.parse_args()
    if args.command == "go":
        stocks.go()
    else:
        print("请输入mock url")


if __name__ == "__main__":
    main()
