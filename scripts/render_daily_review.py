#!/usr/bin/env python3
"""把日复盘 body 渲染成固定 HTML，并按约定归档。

用法：
    python scripts/render_daily_review.py output/review_body.html \
        --title "A 股日复盘 · 数据日 2026-10-27" --date 2026-10-27 --archive

body 是 HTML 片段，自带 masthead / toc / main / footer，并包含 8 个 section：
conclusion / market / themes / rank / strength / watch / bottom / board。
Markdown 也可作为兜底输入，渲染成同一套外壳。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from stocks import review_html


def _read_body(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    source = Path(path).expanduser()
    if not source.is_file():
        raise SystemExit(f"内容文件不存在: {source}")
    return source.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲染固定版式日复盘 HTML")
    parser.add_argument("body", help="HTML body 文件，或 - 从 stdin 读取")
    parser.add_argument("--title", default=None, help="页面标题")
    parser.add_argument(
        "--date",
        dest="data_date",
        default=date.today().isoformat(),
        help="数据日，支持 2026-10-27 / 2026_10_27 / 20261027",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "body", "markdown"),
        default="auto",
        help="auto: 有完整 8 section 时按 body，否则按 Markdown；body: 严格校验；markdown: 强制转换",
    )
    parser.add_argument(
        "--output",
        default="output/review.html",
        help="项目内工作副本路径（默认 output/review.html）",
    )
    archive_group = parser.add_mutually_exclusive_group()
    archive_group.add_argument(
        "--archive",
        dest="archive",
        action="store_true",
        default=True,
        help="归档到桌面（默认开启）",
    )
    archive_group.add_argument(
        "--no-archive",
        dest="archive",
        action="store_false",
        help="只写工作副本，不复制到桌面",
    )
    parser.add_argument("--open", action="store_true", help="归档后打开")
    parser.add_argument(
        "--desktop",
        default=None,
        help="覆盖桌面目录（测试/特殊环境用）",
    )
    args = parser.parse_args(argv)

    try:
        data_date = review_html.parse_date(args.data_date)
    except review_html.ReviewHTMLError as exc:
        parser.error(str(exc))

    title = args.title or f"A 股日复盘 · 数据日 {data_date:%Y-%m-%d}"
    try:
        content = _read_body(args.body)
        dest = review_html.deliver(
            content,
            title=title,
            data_date=data_date,
            output=args.output,
            archive_file=args.archive,
            desktop=args.desktop,
            mode=args.mode,
        )
    except review_html.ReviewHTMLError as exc:
        print(f"HTML 复盘渲染失败: {exc}", file=sys.stderr)
        return 2

    print(dest)
    if args.open:
        review_html.open_file(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
