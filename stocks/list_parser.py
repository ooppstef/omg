from pathlib import Path

from stocks.paths import data_file, exclusive_file_lock, temporary_path_for


DEFAULT_LIST_CONTENT = "# 自选\n\n# 查看\n"
KNOWN_SECTIONS = frozenset({"自选", "查看"})


def my_list_path() -> Path:
    """仓库内自选列表（stocks/my_list.txt，已 gitignore）。"""
    return Path(__file__).with_name("my_list.txt")


def my_list_example_path() -> Path:
    return Path(__file__).with_name("my_list.txt.example")


def initialize_my_list() -> Path:
    """若不存在则从 example（或默认模板）生成 my_list.txt。"""
    path = my_list_path()
    if path.exists():
        return path
    # 锁放用户数据目录，避免污染仓库
    with exclusive_file_lock(data_file("my_list.init.lock")):
        if path.exists():
            return path
        example = my_list_example_path()
        if example.is_file():
            content = example.read_text(encoding="utf-8")
        else:
            content = DEFAULT_LIST_CONTENT
        temporary = temporary_path_for(path)
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def parse_sections(content: str) -> dict:
    sections: dict[str, list[str]] = {}
    current_section = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            name = line[2:].strip()
            if name in KNOWN_SECTIONS:
                current_section = name
                sections.setdefault(current_section, [])
            # 其它「# 」行当作注释；「#nospace」仍可能被当成标的名
            continue
        if line and current_section is not None:
            sections[current_section].append(line)
    return sections


def parse_my_list():
    path = initialize_my_list()
    return parse_sections(path.read_text(encoding="utf-8"))


def get_section_by_name(section_name: str):
    sections = parse_my_list()
    return sections.get(section_name, [])
