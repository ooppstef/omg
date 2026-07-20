import os
from pathlib import Path

from stocks.list_parser import initialize_my_list, my_list_path
from stocks.paths import temporary_path_for


TARGET_ENV_NAME = "OMG_COPY_TARGET_PATH"


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
    source = Path(source_path) if source_path is not None else my_list_path()
    target_value = target_path or os.getenv(TARGET_ENV_NAME)
    if not target_value:
        raise ValueError(f"请通过 {TARGET_ENV_NAME} 环境变量或 target_path 参数指定同步目标文件")
    target = Path(target_value)
    if source.resolve() == target.resolve():
        raise ValueError("同步源文件与目标文件不能相同")

    if source_path is None:
        source = initialize_my_list()
    if not source.exists():
        raise FileNotFoundError(f"源文件不存在: {source}")

    content = source.read_text(encoding="utf-8")
    watch_lines = extract_section_lines(content, section_name)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_path_for(target)
    try:
        temporary.write_text(
            _render_watchlist(watch_lines, section_name),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return len(watch_lines)


def copy_watchlist():
    try:
        count = sync_watchlist()
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(str(error)) from None

    print(f"已同步 {count} 只自选到目标文件")
    return count
