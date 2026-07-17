"""日线数据持久化。"""

import datetime
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from functools import wraps
from threading import RLock, local

import adata
import pandas as pd
from stocks.db import dir_path
from stocks.paths import data_file, exclusive_file_lock

# ---------- 配置 ----------
INIT_DAYS = 200  # 初始化拉取交易日数（约10个月）
HISTORY_MIN_ROWS = 60  # 筛选指标所需的最少历史行数
WORKERS = 20  # 并发线程数
COVERAGE_GATE = 0.90  # 增量更新覆盖率门槛
INIT_COVERAGE_GATE = 0.80  # 全量初始化覆盖率门槛（宽松）

PREFIXES = (
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

DDL = """
CREATE TABLE IF NOT EXISTS DailyQuote (
    code        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
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

_UPDATE_THREAD_LOCK = RLock()
_UPDATE_LOCK_STATE = local()


def _update_lock_path():
    return str(data_file("stocks.update.lock"))


@contextmanager
def _update_lock():
    """串行化跨线程/进程的全量与增量换表。"""
    with _UPDATE_THREAD_LOCK:
        depth = getattr(_UPDATE_LOCK_STATE, "depth", 0)
        if depth:
            _UPDATE_LOCK_STATE.depth = depth + 1
            try:
                yield
            finally:
                _UPDATE_LOCK_STATE.depth -= 1
            return

        with exclusive_file_lock(data_file("stocks.update.lock")):
            _UPDATE_LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _UPDATE_LOCK_STATE.depth = 0


def _serialized_update(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _update_lock():
            return func(*args, **kwargs)

    return wrapped


def _replace_table_atomically(conn, source_table, target_table):
    """在显式事务中交换表，失败时保留原表。"""
    valid_names = {"DailyQuote", "DailyQuote_tmp", "DailyQuote_backup"}
    if source_table not in valid_names or target_table not in valid_names:
        raise ValueError("不支持的表名")
    backup_table = f"{target_table}_backup"
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {backup_table}")
        conn.execute(f"ALTER TABLE {target_table} RENAME TO {backup_table}")
        conn.execute(f"ALTER TABLE {source_table} RENAME TO {target_table}")
        conn.execute(f"DROP TABLE {backup_table}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _conn():
    return sqlite3.connect(dir_path())


def _init_table():
    with _conn() as c:
        c.execute(DDL)


def _all_codes():
    """全市场股票代码（主板/创业板/科创板，排除ST）。"""
    # adata 内部对未知代码前缀(如81开头港股通)会 KeyError，patch 掉
    import sys
    import adata.common.utils.code_utils as _cu
    _orig = _cu.get_exchange_by_stock_code
    def _safe_exchange(code):
        try:
            return _orig(code)
        except KeyError:
            return ""
    _cu.get_exchange_by_stock_code = _safe_exchange
    # adata 的 stock_code 模块用 from import 绑定了局部引用，也要 patch
    _sc = sys.modules.get("adata.stock.info.stock_code")
    if _sc:
        _sc.get_exchange_by_stock_code = _safe_exchange

    df = adata.stock.info.all_code()
    if df is None or df.empty:
        return []
    codes = []
    for _, row in df.iterrows():
        code = str(row["stock_code"])
        name = str(row.get("short_name", ""))
        if code.startswith(PREFIXES) and "ST" not in name.upper():
            codes.append(code)
    return codes


def _start_date(days):
    dt = datetime.date.today() - datetime.timedelta(days=int(days * 1.8))
    return dt.strftime("%Y-%m-%d")


def _cutoff_date():
    return _start_date(INIT_DAYS)


# ---------- 拉取 ----------


def _fetch_one(code, start):
    """拉取单只股票日线，返回 (code, rows, error)。"""
    try:
        df = adata.stock.market.get_market(stock_code=code, start_date=start)
        if df is None or df.empty:
            return code, [], None
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
        return code, rows, None
    except Exception as e:
        return code, [], e


def _batch_fetch(codes, start, conn, table, label, progress):
    """并发拉取写入，返回 (成功数, 失败数, 失败样本)。"""
    total = len(codes)
    done = ok = fail = 0
    fail_samples = []
    batch = []
    sql = f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?)"

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(_fetch_one, c, start): c for c in codes}
        for fut in as_completed(futs):
            done += 1
            code, rows, err = fut.result()
            if err:
                fail += 1
                if len(fail_samples) < 5:
                    fail_samples.append(f"{code}: {err}")
            elif rows:
                ok += 1
                batch.extend(rows)
            else:
                fail += 1
                if len(fail_samples) < 5:
                    fail_samples.append(f"{code}: 返回空行情")
            if progress and (done % 200 == 0 or done == total):
                print(f"  {label}: {done}/{total} (成功:{ok} 失败:{fail})")
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                conn.commit()
                batch.clear()
        if batch:
            conn.executemany(sql, batch)
            conn.commit()

    return ok, fail, fail_samples


def _print_failures(fail, samples, total):
    if fail == 0:
        return
    print(f"  ⚠ 失败 {fail}/{total} ({fail / total * 100:.1f}%)")
    for s in samples:
        print(f"    {s}")
    if fail > len(samples):
        print(f"    ... 及其他 {fail - len(samples)} 只")


# ---------- 覆盖率检查 ----------


def _check_coverage(conn, expected, table="DailyQuote", expected_date=None):
    """返回 (最新日期, 覆盖数, 覆盖率)。"""
    if expected_date is None:
        row = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
        latest = row[0] if row else None
    else:
        latest = expected_date
    if latest is None:
        raise RuntimeError("未拉取到任何日线数据")
    if isinstance(expected, int):
        expected_count = expected
        count = conn.execute(
            f"SELECT COUNT(DISTINCT code) FROM {table} WHERE trade_date = ?",
            (latest,),
        ).fetchone()[0]
    else:
        expected_codes = {str(code) for code in expected}
        expected_count = len(expected_codes)
        actual_codes = {
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT code FROM {table} WHERE trade_date = ?",
                (latest,),
            )
        }
        count = len(expected_codes & actual_codes)
    ratio = count / expected_count if expected_count else 0
    return latest, count, ratio


def _require_coverage(
    conn,
    expected,
    table="DailyQuote",
    gate=COVERAGE_GATE,
    expected_date=None,
):
    """覆盖率不达标则抛异常。"""
    latest, count, ratio = _check_coverage(
        conn,
        expected,
        table,
        expected_date=expected_date,
    )
    if ratio < gate:
        prefix = "目标交易日覆盖率不足" if expected_date is not None else "覆盖率不足"
        expected_count = expected if isinstance(expected, int) else len(set(expected))
        raise RuntimeError(
            f"{prefix}: {latest} 仅 {count}/{expected_count} ({ratio:.1%})"
        )
    return latest, count, ratio


def _check_history_coverage(
    conn,
    expected,
    table="DailyQuote",
    min_rows=HISTORY_MIN_ROWS,
):
    expected_codes = {str(code) for code in expected}
    qualified_codes = {
        str(row[0])
        for row in conn.execute(
            f"""SELECT code FROM {table}
                GROUP BY code
                HAVING COUNT(DISTINCT trade_date) >= ?""",
            (min_rows,),
        )
    }
    count = len(expected_codes & qualified_codes)
    ratio = count / len(expected_codes) if expected_codes else 0
    return count, ratio


# ---------- 目标交易日 ----------


def _target_date():
    """通过沪深300判断当前应追齐到的交易日。15:00前目标是上一交易日。"""
    now = datetime.datetime.now()
    start = (now.date() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        df = adata.stock.market.get_market_index(index_code="000300", start_date=start)
        if df is None or df.empty:
            return None
        dates = df.sort_values("trade_date")["trade_date"].astype(str).str[:10].tolist()
    except Exception:
        return None
    if not dates:
        return None

    today = now.date().strftime("%Y-%m-%d")
    if dates[-1] == today and now.hour < 15:
        return dates[-2] if len(dates) >= 2 else None
    return dates[-1]


def _latest_date():
    """DB 中最新 trade_date，无数据返回 None。"""
    with _conn() as c:
        row = c.execute("SELECT MAX(trade_date) FROM DailyQuote").fetchone()
    return row[0] if row and row[0] else None


# ---------- 全量初始化 ----------


@_serialized_update
def full_init(progress=True, codes=None):
    """全量初始化，写临时表后原子替换。"""
    _init_table()
    codes = codes if codes is not None else _all_codes()
    if not codes:
        print("获取股票列表失败")
        return False

    start = _start_date(INIT_DAYS)
    total = len(codes)
    if progress:
        print(f"全量初始化: {total} 只，起始 {start}")

    conn = _conn()
    conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
    conn.execute(DDL.replace("DailyQuote", "DailyQuote_tmp"))
    conn.commit()

    try:
        ok, fail, samples = _batch_fetch(
            codes, start, conn, "DailyQuote_tmp", "初始化", progress
        )
        latest, count, ratio = _require_coverage(
            conn, codes, "DailyQuote_tmp", INIT_COVERAGE_GATE
        )
        history_count, history_ratio = _check_history_coverage(
            conn,
            codes,
            table="DailyQuote_tmp",
        )
        if history_ratio < INIT_COVERAGE_GATE:
            raise RuntimeError(
                "历史数据覆盖率不足: "
                f"仅 {history_count}/{total} 只达到 {HISTORY_MIN_ROWS} 行 "
                f"({history_ratio:.1%})"
            )
        if progress:
            print(f"  最新交易日 {latest} 覆盖 {count}/{total} ({ratio:.1%})")

        _replace_table_atomically(conn, "DailyQuote_tmp", "DailyQuote")
    except Exception:
        conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
        conn.commit()
        raise
    finally:
        conn.close()

    if progress:
        _print_failures(fail, samples, total)
        print(f"全量初始化完成 (成功 {ok}/{total})")
    return True


# ---------- 增量更新 ----------


@_serialized_update
def incremental_update(progress=True, target_date=None, codes=None):
    """增量更新，写临时表后原子替换。"""
    last = _latest_date()
    if target_date is None:
        target_date = _target_date()
    if target_date is None:
        raise RuntimeError("无法获取目标交易日（网络异常），请检查网络后重试")
    codes = codes if codes is not None else _all_codes()
    if not codes:
        print("获取股票列表失败")
        return False
    if last is None:
        if not full_init(progress, codes=codes):
            return False
        with _conn() as conn:
            _require_coverage(
                conn,
                codes,
                expected_date=target_date,
            )
        return True
    if last >= target_date:
        with _conn() as conn:
            _, _, ratio = _check_coverage(
                conn,
                codes,
                expected_date=target_date,
            )
        if ratio >= COVERAGE_GATE:
            if progress:
                print(f"日线数据已达到目标交易日 {target_date}")
            return True

    total = len(codes)
    if progress:
        print(f"增量更新: {total} 只，从 {last} 补齐")

    conn = _conn()
    conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
    conn.execute(DDL.replace("DailyQuote", "DailyQuote_tmp"))
    conn.execute("INSERT INTO DailyQuote_tmp SELECT * FROM DailyQuote")
    conn.commit()

    try:
        ok, fail, samples = _batch_fetch(
            codes, last, conn, "DailyQuote_tmp", "更新", progress
        )
        latest, count, ratio = _require_coverage(
            conn,
            codes,
            "DailyQuote_tmp",
            expected_date=target_date,
        )
        # 清理过期数据
        cutoff = _cutoff_date()
        conn.execute(f"DELETE FROM DailyQuote_tmp WHERE trade_date < ?", (cutoff,))
        conn.commit()
        if progress:
            print(f"  最新交易日 {latest} 覆盖 {count}/{total} ({ratio:.1%})")

        _replace_table_atomically(conn, "DailyQuote_tmp", "DailyQuote")
    except Exception:
        conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
        conn.commit()
        raise
    finally:
        conn.close()

    if progress:
        _print_failures(fail, samples, total)
        print(f"增量更新完成 (成功 {ok}/{total})")
    return True


# ---------- 对外接口 ----------


def _check_stamp_path():
    return str(data_file("stocks.last_check"))


def _stamp_matches_target(stamp_value, target_date, latest_date):
    return bool(
        stamp_value
        and target_date
        and latest_date
        and stamp_value == target_date
        and latest_date >= target_date
    )


@_serialized_update
def ensure_data(progress=True):
    """确保数据可用且最新。按目标交易日记录检查时间戳。"""
    import os

    stamp = _check_stamp_path()
    _init_table()
    target = _target_date()
    if target is None:
        raise RuntimeError("无法获取目标交易日（网络异常），请检查网络后重试")

    latest = _latest_date()
    codes = _all_codes()
    if not codes:
        raise RuntimeError("获取股票列表失败，无法验证数据完整性")

    if latest is not None:
        with _conn() as conn:
            history_count, history_ratio = _check_history_coverage(conn, codes)
        if history_ratio < INIT_COVERAGE_GATE:
            if progress:
                print(
                    f"仅 {history_count}/{len(codes)} 只股票具备 "
                    f"{HISTORY_MIN_ROWS} 行历史，开始全量重建..."
                )
            if not full_init(progress, codes=codes):
                raise RuntimeError("全量重建历史数据失败")
            latest = _latest_date()
            if latest is None or latest < target:
                raise RuntimeError(f"全量重建后仍未达到目标交易日 {target}")
            with _conn() as conn:
                history_count, history_ratio = _check_history_coverage(conn, codes)
            if history_ratio < INIT_COVERAGE_GATE:
                raise RuntimeError(
                    f"全量重建后历史数据覆盖仍不足: {history_count}/{len(codes)}"
                )

    stamp_value = None
    if os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as f:
            stamp_value = f.read().strip()
    if _stamp_matches_target(stamp_value, target, latest):
        with _conn() as conn:
            _, _, ratio = _check_coverage(
                conn,
                codes,
                expected_date=target,
            )
        if ratio >= COVERAGE_GATE:
            if progress:
                print(f"目标交易日 {target} 已检查，跳过")
            return

    if latest is None:
        if not full_init(progress, codes=codes):
            raise RuntimeError("初始化历史数据失败")
        latest = _latest_date()
        if latest is None or latest < target:
            raise RuntimeError(f"初始化后仍未达到目标交易日 {target}")

    if latest < target:
        if progress:
            print(f"数据落后，目标交易日 {target}，开始增量更新...")
        if not incremental_update(progress, target_date=target, codes=codes):
            raise RuntimeError(f"更新目标交易日 {target} 失败")
        updated = _latest_date()
        if updated is None or updated < target:
            raise RuntimeError(f"更新后仍未达到目标交易日 {target}")
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(target)
        return

    # 日期最新，检查覆盖率（分母用全市场应有数，防止残缺库自我验证100%）
    expected = len(codes)
    conn = _conn()
    latest, count, ratio = _check_coverage(
        conn,
        codes,
        expected_date=target,
    )
    conn.close()

    if ratio < COVERAGE_GATE:
        if progress:
            print(
                f"覆盖 {count}/{expected} ({ratio:.1%})，低于 {COVERAGE_GATE:.0%}，增量补齐"
            )
        if not incremental_update(progress, target_date=target, codes=codes):
            raise RuntimeError(f"补齐目标交易日 {target} 失败")
    else:
        if progress:
            print("日线数据已是最新")

    with open(stamp, "w", encoding="utf-8") as f:
        f.write(target)


def load_all(min_rows=60):
    """加载全部日线，返回 {code: DataFrame}，过滤不足 min_rows 的。"""
    conn = _conn()
    df = pd.read_sql("SELECT * FROM DailyQuote ORDER BY code, trade_date", conn)
    conn.close()
    if df.empty:
        return {}
    return {
        code: group.reset_index(drop=True)
        for code, group in df.groupby("code")
        if len(group) >= min_rows
    }
