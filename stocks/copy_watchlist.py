import os
from pathlib import Path


DEFAULT_TARGET_PATH = Path(
    "/Users/charles/Desktop/deer-flow/skills/custom/a-share-review/references/holdings-watchlist.txt"
)
TARGET_ENV_NAME = "OMG_COPY_TARGET_PATH"


def _default_source_path():
    return Path(__file__).with_name("my_list.txt")


def extract_section_lines(content, section_name):
    lines = content.splitlines()
    current_section = None
    found = False
    result = []

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("# "):
            current_section = line[2:].strip()
            if found:
                break
            if current_section == section_name:
                found = True
            continue

        if found and current_section == section_name and line:
            result.append(line)

    if not found:
        raise ValueError(f"未找到 #{section_name} section")

    return result


def _render_watchlist(lines, section_name="自选"):
    body = "\n".join(lines)
    if body:
        return f"# {section_name}\n{body}\n"
    return f"# {section_name}\n"


def sync_watchlist(source_path=None, target_path=None, section_name="自选"):
    source = Path(source_path) if source_path is not None else _default_source_path()
    target_value = target_path if target_path is not None else os.getenv(TARGET_ENV_NAME)
    target = Path(target_value) if target_value else DEFAULT_TARGET_PATH

    if not source.exists():
        raise FileNotFoundError(f"源文件不存在: {source}")

    content = source.read_text(encoding="utf-8")
    watch_lines = extract_section_lines(content, section_name)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_watchlist(watch_lines, section_name), encoding="utf-8")
    return len(watch_lines)


def copy_watchlist():
    count = sync_watchlist()
    print(f"已同步 {count} 只自选到 DeerFlow skill")

