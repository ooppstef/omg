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
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


def desktop_dir() -> Path:
    """返回当前用户的桌面目录；找不到时创建 ~/Desktop。"""
    home = Path.home()
    candidates: list[Path] = []

    if sys.platform.startswith("win"):
        for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            value = os.environ.get(env)
            if value:
                candidates.append(Path(value) / "Desktop")
        profile = os.environ.get("USERPROFILE")
        if profile:
            candidates.append(Path(profile) / "Desktop")
        candidates.append(home / "Desktop")
    else:
        xdg = os.environ.get("XDG_DESKTOP_DIR")
        if xdg:
            candidates.append(Path(xdg).expanduser())
        candidates.append(home / "Desktop")

    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    fallback = home / "Desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def parse_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    for fmt in ("%Y-%m-%d", "%Y_%m_%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"无法识别日期: {raw}（支持 2026-10-26 / 2026_10_26 / 20261026）")


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


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

    data_date = parse_date(args.data_date)
    folder = desktop_dir() / f"{date.today():%Y_%m_%d}_复盘"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{data_date:%Y_%m_%d}_复盘.html"

    existed = dest.exists()
    if source != dest.resolve():
        shutil.copy2(source, dest)

    print(f"{'覆盖' if existed else '已归档'}: {dest}")
    if args.open:
        open_file(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
