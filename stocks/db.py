import os
import adata
import sqlite3
from threading import RLock
from pypinyin import pinyin, Style
from stocks.paths import data_file
from stocks.stock_models import StockBase

_INIT_LOCK = RLock()
_INITIALIZED_PATHS = set()
BASE_DATA_MIN_COUNT = 4000
BASE_DATA_REQUIRED_EXCHANGES = {"SH", "SZ"}


def dir_path():
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.db")
    return str(data_file("stocks.db", legacy_path=legacy))


def init_table():
    path = dir_path()
    with _INIT_LOCK:
        conn = sqlite3.connect(path)
        try:
            with conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS Base (
                        id INTEGER PRIMARY KEY,
                        code TEXT,
                        name TEXT,
                        short TEXT,
                        exchange TEXT
                    )"""
                )
                conn.execute(
                    """DELETE FROM Base
                       WHERE id NOT IN (
                           SELECT MIN(id) FROM Base GROUP BY code
                       )"""
                )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_base_code ON Base(code)"
                )
        finally:
            conn.close()
        _INITIALIZED_PATHS.add(path)


def _ensure_table():
    path = dir_path()
    if path not in _INITIALIZED_PATHS or not os.path.exists(path):
        init_table()


def _query(query, params, fetch_all):
    for attempt in range(2):
        _ensure_table()
        try:
            with sqlite3.connect(dir_path()) as conn:
                cursor = conn.execute(query, params)
                return cursor.fetchall() if fetch_all else cursor.fetchone()
        except sqlite3.OperationalError as exc:
            if attempt or "no such table: Base" not in str(exc):
                raise
            _INITIALIZED_PATHS.discard(dir_path())
    return [] if fetch_all else None


def _query_one(query, params):
    result = _query(query, params, fetch_all=False)
    return StockBase(*result) if result else None


def _query_all(query, params):
    results = _query(query, params, fetch_all=True)
    return [StockBase(*result) for result in results]


def insert_base_batch(data):
    init_table()
    conn = sqlite3.connect(dir_path())
    try:
        with conn:
            conn.executemany(
                """INSERT INTO Base (code, name, short, exchange)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(code) DO UPDATE SET
                       name=excluded.name,
                       short=excluded.short,
                       exchange=excluded.exchange""",
                data,
            )
    finally:
        conn.close()


def refresh_base_data():
    data = get_base_data()
    if not data:
        raise RuntimeError("未获取到股票基础数据")
    unique_codes = {str(row[0]) for row in data}
    exchanges = {str(row[3]).upper() for row in data}
    if (
        len(unique_codes) < BASE_DATA_MIN_COUNT
        or not BASE_DATA_REQUIRED_EXCHANGES.issubset(exchanges)
    ):
        raise RuntimeError(
            "股票基础数据不完整: "
            f"仅 {len(unique_codes)} 只，交易所 {sorted(exchanges)}"
        )
    insert_base_batch(data)
    return len(data)


def get_base_data():
    res_df = adata.stock.info.all_code()
    required_columns = {"stock_code", "short_name", "exchange"}
    if (
        res_df is None
        or res_df.empty
        or not required_columns.issubset(res_df.columns)
    ):
        return []
    stock_list = res_df[["stock_code", "short_name", "exchange"]].values.tolist()
    for stock in stock_list:
        name = stock[1]
        short = chinese_to_acronym(name)
        stock.insert(2, short)

    by_exchange = {"SH": [], "SZ": [], "BJ": []}
    for stock in stock_list:
        exchange = stock[3]
        if exchange in by_exchange:
            by_exchange[exchange].append(stock)

    sorted_stock_list = []
    for exchange in ("SH", "SZ", "BJ"):
        sorted_stock_list.extend(by_exchange[exchange])
    return sorted_stock_list


def chinese_to_acronym(chinese_str):
    pinyin_list = pinyin(chinese_str, style=Style.FIRST_LETTER)
    return "".join(item[0].upper() for item in pinyin_list)


def search_base_by_code(code):
    return _query_one("SELECT * FROM Base WHERE code = ?", (code,))


def search_base_by_short(short):
    short = short.upper()
    return _query_one("SELECT * FROM Base WHERE short = ?", (short,))


def search_base_by_shorts(shorts):
    shorts = [short.upper() for short in shorts]
    if not shorts:
        return []
    placeholders = ", ".join(["?" for _ in shorts])
    query = f"SELECT * FROM Base WHERE short IN ({placeholders})"
    return _query_all(query, shorts)


def search_base_by_name(name):
    return _query_one("SELECT * FROM Base WHERE name = ?", (name,))


def search_base_by_names(names):
    if not names:
        return []
    placeholders = ", ".join(["?" for _ in names])
    query = f"SELECT * FROM Base WHERE name IN ({placeholders})"
    return _query_all(query, names)


def search_base_by_names_like(names):
    if isinstance(names, str):
        names = [names]
    if not names:
        return []
    placeholders = " OR ".join(["name LIKE ?" for _ in names])
    query = f"SELECT * FROM Base WHERE {placeholders}"
    params = [f"%{name}%" for name in names]
    return _query_all(query, params)


def search_base_by_short_prefix(short_prefix):
    short_prefix = short_prefix.upper() + "%"
    return _query_all("SELECT * FROM Base WHERE short LIKE ?", (short_prefix,))
