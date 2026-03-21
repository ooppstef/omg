"""日线数据持久化：建表、全量初始化、增量更新、查询。"""

import datetime
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import adata
import pandas as pd

from stocks.db import dir_path

# 常量
INIT_DAYS = 60
UPDATE_WORKERS = 20
MIN_LATEST_DATE_COVERAGE = 0.98
VALID_PREFIXES = (
    "600",
    "601",
    "603",
    "605",
    "688",
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
)

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS DailyQuote (
    code        TEXT    NOT NULL,
    trade_date  TEXT    NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    amount      REAL,
    change_pct  REAL,
    turnover    REAL,
    PRIMARY KEY (code, trade_date)
)
"""


def _conn():
    return sqlite3.connect(dir_path())


def init_table():
    """建表（幂等）。"""
    conn = _conn()
    conn.execute(TABLE_DDL)
    conn.commit()
    conn.close()


def _get_all_codes():
    """获取全市场股票代码，过滤为主板+创业板+科创板，排除ST。"""
    df = adata.stock.info.all_code()
    if df is None or df.empty:
        return []
    codes = []
    for _, row in df.iterrows():
        code = str(row["stock_code"])
        name = str(row.get("short_name", ""))
        if not code.startswith(VALID_PREFIXES):
            continue
        if "ST" in name.upper():
            continue
        codes.append(code)
    return codes


def _start_date(days):
    """计算起始日期字符串。"""
    dt = datetime.date.today() - datetime.timedelta(days=int(days * 1.8))
    return dt.strftime("%Y-%m-%d")


class _FetchResult:
    """单只股票拉取结果，区分成功/空数据/异常。"""

    __slots__ = ("code", "rows", "error")

    def __init__(self, code, rows=None, error=None):
        self.code = code
        self.rows = rows or []
        self.error = error

    @property
    def ok(self):
        return self.error is None


def _fetch_one(code, start_date):
    """拉取单只股票日线数据，返回 _FetchResult。"""
    try:
        df = adata.stock.market.get_market(stock_code=code, start_date=start_date)
        if df is None or df.empty:
            return _FetchResult(code)
        rows = []
        for _, r in df.iterrows():
            rows.append(
                (
                    code,
                    str(r["trade_date"])[:10],
                    float(r.get("open", 0) or 0),
                    float(r.get("high", 0) or 0),
                    float(r.get("low", 0) or 0),
                    float(r.get("close", 0) or 0),
                    float(r.get("volume", 0) or 0),
                    float(r.get("amount", 0) or 0),
                    float(r.get("change_pct", 0) or 0),
                    float(r.get("turnover_ratio", 0) or 0),
                )
            )
        return _FetchResult(code, rows)
    except Exception as e:
        return _FetchResult(code, error=e)


def _run_fetch(codes, start, conn, progress, label, table="DailyQuote"):
    """并发拉取并写入，返回 (成功数, 失败数, 失败样本)。"""
    total = len(codes)
    completed = 0
    ok_count = 0
    fail_count = 0
    fail_samples = []
    batch = []
    sql = f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?)"

    with ThreadPoolExecutor(max_workers=UPDATE_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, code, start): code for code in codes}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if not result.ok:
                fail_count += 1
                if len(fail_samples) < 5:
                    fail_samples.append(f"{result.code}: {result.error}")
            elif result.rows:
                ok_count += 1
                batch.extend(result.rows)
            if progress and (completed % 100 == 0 or completed == total):
                print(f"  {label}进度: {completed}/{total}")
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                conn.commit()
                batch.clear()
        if batch:
            conn.executemany(sql, batch)
            conn.commit()

    return ok_count, fail_count, fail_samples


def _report_failures(fail_count, fail_samples, total, progress):
    """打印失败统计。"""
    if not progress or fail_count == 0:
        return
    ratio = fail_count / total * 100 if total else 0
    print(f"  ⚠ 拉取失败 {fail_count}/{total} ({ratio:.1f}%)")
    for s in fail_samples:
        print(f"    {s}")
    if fail_count > len(fail_samples):
        print(f"    ... 及其他 {fail_count - len(fail_samples)} 只")


def _latest_trade_date_coverage(conn, table="DailyQuote"):
    """返回表中最新交易日及该日覆盖的股票数。"""
    latest_date = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()[0]
    if latest_date is None:
        return None, 0
    latest_count = conn.execute(
        f"SELECT COUNT(DISTINCT code) FROM {table} WHERE trade_date = ?",
        (latest_date,),
    ).fetchone()[0]
    return latest_date, latest_count


def _require_latest_date_coverage(
    conn,
    expected_codes,
    table="DailyQuote",
    min_ratio=MIN_LATEST_DATE_COVERAGE,
):
    """要求最新交易日覆盖率达到阈值，避免部分更新污染筛选样本。"""
    latest_date, latest_count = _latest_trade_date_coverage(conn, table)
    if latest_date is None:
        raise RuntimeError("未拉取到任何日线数据")
    ratio = latest_count / expected_codes if expected_codes else 0
    if ratio < min_ratio:
        raise RuntimeError(
            f"最新交易日覆盖率不足: {latest_date} 仅 {latest_count}/{expected_codes}"
            f" ({ratio:.1%})"
        )
    return latest_date, latest_count, ratio


def _trim_old_data_in_table(conn, table="DailyQuote"):
    """删除超出保留窗口的旧数据。"""
    cutoff = _start_date(INIT_DAYS)
    conn.execute(f"DELETE FROM {table} WHERE trade_date < ?", (cutoff,))
    conn.commit()


def full_init(progress=True):
    """全量初始化：拉取全市场近 INIT_DAYS 天日线数据。

    先写入临时表，成功后原子替换，避免中断导致残缺。
    """
    init_table()
    codes = _get_all_codes()
    if not codes:
        print("获取股票列表失败")
        return
    start = _start_date(INIT_DAYS)
    total = len(codes)
    if progress:
        print(f"全量初始化: {total} 只股票，起始日期 {start}")

    conn = _conn()
    # 写入临时表，成功后替换正式表
    conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
    conn.execute(TABLE_DDL.replace("DailyQuote", "DailyQuote_tmp"))
    conn.commit()

    try:
        ok, fail, samples = _run_fetch(
            codes, start, conn, progress, "初始化", table="DailyQuote_tmp"
        )
        latest_trade_date, latest_count, ratio = _require_latest_date_coverage(
            conn, total, table="DailyQuote_tmp"
        )
        if progress:
            print(
                f"  最新交易日 {latest_trade_date} 覆盖 {latest_count}/{total} ({ratio:.1%})"
            )

        conn.execute("DROP TABLE IF EXISTS DailyQuote")
        conn.execute("ALTER TABLE DailyQuote_tmp RENAME TO DailyQuote")
        conn.commit()
    except Exception:
        conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
        conn.commit()
        raise
    finally:
        conn.close()

    _report_failures(fail, samples, total, progress)
    if progress:
        print(f"全量初始化完成 (成功 {ok}/{total})")


def latest_date():
    """查询 DB 中最新的 trade_date，无数据返回 None。"""
    conn = _conn()
    cur = conn.execute("SELECT MAX(trade_date) FROM DailyQuote")
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return None


def _target_date():
    """计算当前应追齐到的目标交易日。

    通过沪深300指数日线获取最近交易日，避免节假日误判。
    工作日 15:00 前目标是上一个交易日，15:00 后目标是当天（如果是交易日）。
    """
    now = datetime.datetime.now()

    # 拉最近 10 天的沪深300日线，取最新 trade_date
    start = (now.date() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        df = adata.stock.market.get_market_index(index_code="000300", start_date=start)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date")
        dates = df["trade_date"].astype(str).str[:10].tolist()
    except Exception:
        return None

    if not dates:
        return None

    today_str = now.date().strftime("%Y-%m-%d")
    latest_trade_date = dates[-1]

    if latest_trade_date == today_str and now.hour < 15:
        # 今天是交易日但还没收盘，目标是上一个交易日
        return dates[-2] if len(dates) >= 2 else None

    return latest_trade_date


def needs_update():
    """判断是否需要更新数据。"""
    last = latest_date()
    if last is None:
        return True
    target = _target_date()
    if target is None:
        # 无法确定目标日期（网络问题等），跳过更新
        return False
    return last < target


def incremental_update(progress=True):
    """增量更新：补齐 DB 最新日期之后的数据。"""
    last = latest_date()
    if last is None:
        full_init(progress)
        return

    codes = _get_all_codes()
    if not codes:
        print("获取股票列表失败")
        return

    start = last
    total = len(codes)
    if progress:
        print(f"增量更新: {total} 只股票，从 {start} 补齐")

    conn = _conn()
    conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
    conn.execute(TABLE_DDL.replace("DailyQuote", "DailyQuote_tmp"))
    conn.execute("INSERT INTO DailyQuote_tmp SELECT * FROM DailyQuote")
    conn.commit()

    try:
        ok, fail, samples = _run_fetch(
            codes, start, conn, progress, "更新", table="DailyQuote_tmp"
        )
        latest_trade_date, latest_count, ratio = _require_latest_date_coverage(
            conn, total, table="DailyQuote_tmp"
        )
        _trim_old_data_in_table(conn, table="DailyQuote_tmp")
        if progress:
            print(
                f"  最新交易日 {latest_trade_date} 覆盖 {latest_count}/{total} ({ratio:.1%})"
            )

        conn.execute("DROP TABLE IF EXISTS DailyQuote")
        conn.execute("ALTER TABLE DailyQuote_tmp RENAME TO DailyQuote")
        conn.commit()
    except Exception:
        conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
        conn.commit()
        raise
    finally:
        conn.close()

    _report_failures(fail, samples, total, progress)
    if progress:
        print(f"增量更新完成 (成功 {ok}/{total})")


def _trim_old_data():
    """删除超出保留窗口的旧数据。"""
    conn = _conn()
    _trim_old_data_in_table(conn)
    conn.close()


def ensure_data(progress=True):
    """确保数据可用且最新。供筛选入口调用。"""
    init_table()
    if latest_date() is None:
        full_init(progress)
    elif needs_update():
        incremental_update(progress)
    else:
        if progress:
            print("日线数据已是最新")


def load_all(min_rows=30):
    """加载全部日线数据，返回 {code: DataFrame}，过滤掉数据不足 min_rows 的。"""
    conn = _conn()
    df = pd.read_sql("SELECT * FROM DailyQuote ORDER BY code, trade_date", conn)
    conn.close()
    if df.empty:
        return {}
    result = {}
    for code, group in df.groupby("code"):
        if len(group) >= min_rows:
            result[code] = group.reset_index(drop=True)
    return result
