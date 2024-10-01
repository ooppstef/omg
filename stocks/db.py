import os
import adata
import sqlite3
from pypinyin import pinyin, Style
from stocks.stock_models import StockBase


def dir_path():
    # 获取当前脚本所在的目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 构建数据库文件的完整路径
    db_path = os.path.join(current_dir, "stocks.db")
    return db_path


def db_init():
    conn = sqlite3.connect(dir_path())
    conn.close()


def init_table():
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS Base (
            id INTEGER PRIMARY KEY,
            code TEXT,
            name TEXT,
            short TEXT,
            exchange TEXT
        )"""
    )
    conn.close()


def insert_base_batch(data):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()

    # 将数据分成多个小批次进行插入
    batch_size = 1000  # 每批插入1000条数据
    for i in range(0, len(data), batch_size):
        batch = data[i : i + batch_size]
        cursor.executemany(
            """INSERT INTO Base (code, name, short, exchange) VALUES (?, ?, ?, ?)""",
            batch,
        )
        conn.commit()

    conn.close()


def get_base_data():
    res_df = adata.stock.info.all_code()
    stock_list = res_df[["stock_code", "short_name", "exchange"]].values.tolist()
    for stock in stock_list:
        name = stock[1]
        short = chinese_to_acronym(name)
        stock.insert(2, short)

    # 根据交易所对股票列表进行分类
    classified_stocks = {"SH": [], "SZ": [], "BJ": []}
    for stock in stock_list:
        exchange = stock[3]
        if exchange in classified_stocks:
            classified_stocks[exchange].append(stock)

    # 按照SH>SZ>BJ的顺序重新组合股票列表
    sorted_stock_list = []
    for exchange in ["SH", "SZ", "BJ"]:
        sorted_stock_list.extend(classified_stocks[exchange])

    return sorted_stock_list


def chinese_to_acronym(chinese_str):
    # 将中文转换为拼音
    pinyin_list = pinyin(chinese_str, style=Style.FIRST_LETTER)
    # 提取每个字的首字母并连接
    acronym = "".join(item[0].upper() for item in pinyin_list)
    return acronym


def search_base_by_code(code):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Base WHERE code = ?", (code,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return StockBase(*result)
    return None


def search_base_by_short(short):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    short = short.upper()
    cursor.execute("SELECT * FROM Base WHERE short = ?", (short,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return StockBase(*result)
    return None


def search_base_by_shorts(shorts):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    shorts = [short.upper() for short in shorts]
    placeholders = ", ".join(["?" for _ in shorts])
    query = f"SELECT * FROM Base WHERE short IN ({placeholders})"
    cursor.execute(query, shorts)
    results = cursor.fetchall()
    conn.close()
    return [StockBase(*result) for result in results] if results else []


def search_base_by_name(name):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Base WHERE name = ?", (name,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return StockBase(*result)
    return None


def search_base_by_names(names):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    placeholders = ", ".join(["?" for _ in names])
    query = f"SELECT * FROM Base WHERE name IN ({placeholders})"
    cursor.execute(query, names)
    results = cursor.fetchall()
    conn.close()
    return [StockBase(*result) for result in results] if results else []


def search_base_by_names_like(names):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    if isinstance(names, str):
        names = [names]
    placeholders = " OR ".join(["name LIKE ?" for _ in names])
    query = f"SELECT * FROM Base WHERE {placeholders}"
    params = [f"%{name}%" for name in names]
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    return [StockBase(*result) for result in results] if results else []


def search_base_by_short_prefix(short_prefix):
    conn = sqlite3.connect(dir_path())
    cursor = conn.cursor()
    short_prefix = short_prefix.upper() + "%"
    cursor.execute("SELECT * FROM Base WHERE short LIKE ?", (short_prefix,))
    results = cursor.fetchall()
    conn.close()
    return [StockBase(*result) for result in results] if results else []
