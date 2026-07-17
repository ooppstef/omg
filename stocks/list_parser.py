from pathlib import Path

from stocks.paths import data_file, exclusive_file_lock, temporary_path_for


DEFAULT_LIST_CONTENT = "# 自选\n\n# 查看\n"


def my_list_path():
    legacy = Path(__file__).with_name("my_list.txt")
    return data_file("my_list.txt", legacy_path=legacy)


def initialize_my_list():
    path = my_list_path()
    if path.exists():
        return path
    with exclusive_file_lock(data_file("my_list.init.lock")):
        if not path.exists():
            temporary = temporary_path_for(path)
            try:
                temporary.write_text(DEFAULT_LIST_CONTENT, encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    return path


def parse_my_list():
    path = initialize_my_list()

    sections = {}
    current_section = None

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line.startswith("# "):
                current_section = line[2:].strip()
                sections[current_section] = []
            elif line and current_section is not None:
                sections[current_section].append(line)

    return sections


def get_section_by_name(name):
    sections = parse_my_list()
    return sections.get(name, [])
