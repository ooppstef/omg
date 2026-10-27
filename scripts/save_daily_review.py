#!/usr/bin/env python3
"""把 A 股日复盘 HTML 归档到桌面。

默认约定：
- 目录：桌面/<运行日期 YYYY_MM_DD>_复盘/
- 文件：<数据日 YYYY_MM_DD>_复盘.html

这样盘前归档 10/26 收盘复盘时，目录是 2026_10_27_复盘，
文件名仍是 2026_10_26_复盘.html，避免把数据日写成生成日。

macOS / Windows 都会自动找当前用户的 Desktop（Windows 优先 OneDrive Desktop）。

用法：
    python scripts/save_daily_review.py output/review.html --date 2026-10-26
    python scripts/save_daily_review.py output/review.html --date 2026-10-27 --open

渲染 + 归档一步到位：
    python scripts/render_daily_review.py output/review_body.html --date 2026-10-27 --archive
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from stocks import review_html


def main() -> int:
    parser = argparse.ArgumentParser(description="把日复盘 HTML 归档到桌面")
    parser.add_argument("source", type=Path, help="待归档的 HTML 文件")
    parser.add_argument(
        "--date",
        dest="data_date",
        help="复盘对应的数据日，默认今天；支持 2026-10-26 / 2026_10_26",
    )
    parser.add_argument("--open", action="store_true", help="归档后打开文件")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"文件不存在: {source}")

    try:
        data_date = review_html.parse_date(args.data_date)
    except review_html.ReviewHTMLError as exc:
        raise SystemExit(str(exc)) from exc

    dest = (
        review_html.desktop_dir()
        / f"{date.today():%Y_%m_%d}_复盘"
        / f"{data_date:%Y_%m_%d}_复盘.html"
    )
    existed = dest.exists()
    dest = review_html.archive(source, data_date)
    print(f"{'覆盖' if existed else '已归档'}: {dest}")
    if args.open:
        review_html.open_file(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
