import os


def parse_my_list():
    # 获取当前脚本所在的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 构建my_list.txt文件的完整路径
    my_list_path = os.path.join(current_dir, "my_list.txt")

    sections = {}
    current_section = None

    with open(my_list_path, "r", encoding="utf-8") as file:
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
