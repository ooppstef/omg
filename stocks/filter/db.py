"""日线数据持久化。"""

import datetime
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from functools import wraps
from threading import RLock, local
from zoneinfo import ZoneInfo

import adata
import pandas as pd
from stocks.db import dir_path
from stocks.paths import data_file, exclusive_file_lock
from stocks.universe import A_SHARE_CODE_PREFIXES
from stocks.util import recent_date

# ---------- 配置 ----------
INIT_DAYS = 200  # 初始化拉取交易日数（约10个月）
HISTORY_MIN_ROWS = 60  # 筛选指标所需的最少历史行数
WORKERS = 20  # 并发线程数
COVERAGE_GATE = 0.90  # 增量更新覆盖率门槛
INIT_COVERAGE_GATE = 0.80  # 全量初始化覆盖率门槛（宽松）
PREFIXES = A_SHARE_CODE_PREFIXES
_TZ = ZoneInfo("Asia/Shanghai")
_QUOTE_COLUMNS = (
    "code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "change_pct",
    "turnover",
)
_NUMERIC_QUOTE_COLUMNS = _QUOTE_COLUMNS[2:]
_REQUIRED_QUOTE_NUMERIC = ("open", "high", "low", "close", "volume")
_VALID_QUOTE_SQL = (
    "open > 0 AND high > 0 AND low > 0 AND close > 0 "
    "AND volume > 0 AND change_pct IS NOT NULL "
    "AND high >= open AND high >= close AND high >= low "
    "AND low <= open AND low <= close AND low <= high"
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
_EXCHANGE_PATCH_LOCK = RLock()
_QUOTE_TABLES = frozenset({"DailyQuote", "DailyQuote_tmp"})


def _quote_table_name(table):
    if table not in _QUOTE_TABLES:
        raise ValueError(f"不支持的行情表名: {table}")
    return table


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
    source_table = _quote_table_name(source_table)
    target_table = _quote_table_name(target_table)
    if source_table == target_table:
        raise ValueError("源表与目标表不能相同")
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


@contextmanager
def _connect():
    """连接上下文：事务语义同 `with conn:`，并保证退出时 close。

    `with sqlite3.connect(...) as conn:` 只提交/回滚事务、不关闭连接，
    连接对象会进入引用环，只有循环 GC 才回收；Windows 上句柄不释放会导致
    临时库文件删不掉、原子换表 os.replace 失败。
    """
    conn = _conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init_table():
    with _connect() as c:
        c.execute(DDL)


@contextmanager
def _safe_exchange_patch():
    """在 all_code() 调用期间临时兜底 adata 的交易所代码解析。

    adata 对未知代码前缀(如 81 开头港股通)的 get_exchange_by_stock_code 会抛 KeyError。
    原实现直接覆盖模块属性，是进程级永久副作用；改为仅在调用期间生效、退出即恢复。

    专用 RLock 覆盖整个 patch 窗口，使直接调用 _all_codes() 时也不会出现两个
    补丁交错恢复；无需依赖调用方恰好持有更新锁。
    """
    import sys

    import adata.common.utils.code_utils as _cu

    with _EXCHANGE_PATCH_LOCK:
        original_cu = _cu.get_exchange_by_stock_code
        # adata 的 stock_code 模块用 from import 绑定了局部引用，也要 patch；
        # 用 sys.modules 探测，避免该子模块缺失时直接 import 抛错。
        _sc_mod = sys.modules.get("adata.stock.info.stock_code")
        original_sc = (
            getattr(_sc_mod, "get_exchange_by_stock_code", None)
            if _sc_mod
            else None
        )

        def _safe_exchange(code):
            try:
                return original_cu(code)
            except KeyError:
                return ""

        _cu.get_exchange_by_stock_code = _safe_exchange
        if original_sc is not None:
            _sc_mod.get_exchange_by_stock_code = _safe_exchange
        try:
            yield
        finally:
            _cu.get_exchange_by_stock_code = original_cu
            if original_sc is not None:
                _sc_mod.get_exchange_by_stock_code = original_sc


def _all_codes():
    """全市场股票代码（主板/创业板/科创板，排除ST）。"""
    with _safe_exchange_patch():
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
    return recent_date(int(days * 1.8))


def _cutoff_date():
    return _start_date(INIT_DAYS)


# ---------- 拉取 ----------


def _sanitize_quote_frame(frame):
    """剔除无法用于 OHLCV/技术指标计算的行情行。"""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=_QUOTE_COLUMNS)
    if not set(_QUOTE_COLUMNS).issubset(frame.columns):
        return pd.DataFrame(columns=_QUOTE_COLUMNS)

    out = frame.loc[:, _QUOTE_COLUMNS].copy()
    out["code"] = out["code"].astype(str).str.strip()
    out["trade_date"] = out["trade_date"].astype(str).str[:10]
    parsed_dates = pd.to_datetime(out["trade_date"], errors="coerce")
    for column in _NUMERIC_QUOTE_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.replace([float("inf"), float("-inf")], pd.NA)
    out = out.dropna(subset=list(_REQUIRED_QUOTE_NUMERIC))
    parsed_dates = parsed_dates.reindex(out.index)

    price_cols = ["open", "high", "low", "close"]
    valid = (out[price_cols] > 0).all(axis=1)
    valid &= out["volume"] > 0
    valid &= out["high"] >= out[["open", "close", "low"]].max(axis=1)
    valid &= out["low"] <= out[["open", "close", "high"]].min(axis=1)
    valid &= parsed_dates.notna()
    valid &= out["code"].str.fullmatch(r"\d{6}", na=False)
    valid &= out["code"].str.startswith(PREFIXES)
    out = out.loc[valid].copy()
    out["trade_date"] = parsed_dates.loc[valid].dt.strftime("%Y-%m-%d")
    out = out.drop_duplicates(subset=["code", "trade_date"], keep="last")
    out = out.sort_values(["code", "trade_date"]).reset_index(drop=True)

    # change_pct 可由同一复权序列的相邻收盘价恢复；首行仍缺失则剔除。
    computed_change = (
        out.groupby("code", sort=False)["close"]
        .pct_change(fill_method=None)
        .mul(100)
    )
    out["change_pct"] = out["change_pct"].fillna(computed_change)
    out = out.dropna(subset=["change_pct"]).copy()

    # amount/turnover 不参与技术指标计算；缺失时保留为空，不能反过来
    # 删除有效 OHLCV，也不能用 0 冒充真实观测。
    out["amount"] = out["amount"].where(out["amount"] > 0)
    out["turnover"] = out["turnover"].where(out["turnover"] >= 0)
    return out.reset_index(drop=True)


def _fetch_one(code, start):
    """拉取单只股票日线，返回 (code, rows, error)。"""
    try:
        df = adata.stock.market.get_market(stock_code=code, start_date=start)
        if df is None or df.empty:
            return code, [], None
        frame = df.copy()
        frame["code"] = code
        if "turnover" not in frame.columns and "turnover_ratio" in frame.columns:
            frame["turnover"] = frame["turnover_ratio"]
        for column in _QUOTE_COLUMNS:
            if column not in frame.columns:
                frame[column] = None
        valid = _sanitize_quote_frame(frame)
        rows = [
            tuple(None if pd.isna(value) else value for value in values)
            for values in valid.loc[:, _QUOTE_COLUMNS].itertuples(
                index=False,
                name=None,
            )
        ]
        return code, rows, None
    except Exception as e:
        return code, [], e


def _batch_fetch(codes, start, conn, table, label, progress):
    """并发拉取写入，返回 (成功数, 失败数, 失败样本)。"""
    table = _quote_table_name(table)
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
    table = _quote_table_name(table)
    if expected_date is None:
        # SQLite cannot bind identifiers; table has passed the allowlist.
        row = conn.execute(
            f"SELECT MAX(trade_date) FROM {table}"  # nosec
        ).fetchone()
        latest = row[0] if row else None
    else:
        latest = expected_date
    if latest is None:
        raise RuntimeError("未拉取到任何日线数据")
    if isinstance(expected, int):
        expected_count = expected
        count = conn.execute(
            f"SELECT COUNT(DISTINCT code) FROM {table} "  # nosec
            f"WHERE trade_date = ? AND {_VALID_QUOTE_SQL}",
            (latest,),
        ).fetchone()[0]
    else:
        expected_codes = {str(code) for code in expected}
        expected_count = len(expected_codes)
        actual_codes = {
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT code FROM {table} "  # nosec
                f"WHERE trade_date = ? AND {_VALID_QUOTE_SQL}",
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
    table = _quote_table_name(table)
    expected_codes = {str(code) for code in expected}
    # SQLite cannot bind identifiers; table has passed the allowlist.
    query = (
        f"SELECT code FROM {table} "  # nosec
        f"WHERE {_VALID_QUOTE_SQL} "
        "GROUP BY code "
        "HAVING COUNT(DISTINCT trade_date) >= ?"
    )
    qualified_codes = {
        str(row[0])
        for row in conn.execute(query, (min_rows,))
    }
    count = len(expected_codes & qualified_codes)
    ratio = count / len(expected_codes) if expected_codes else 0
    return count, ratio


# ---------- 目标交易日 ----------


def _target_date():
    """通过沪深300判断当前应追齐到的交易日。15:00前目标是上一交易日。"""
    now = datetime.datetime.now(_TZ)
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
    with _connect() as c:
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
        with _connect() as conn:
            _require_coverage(
                conn,
                codes,
                expected_date=target_date,
            )
        return True
    if last >= target_date:
        with _connect() as conn:
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
        conn.execute("DELETE FROM DailyQuote_tmp WHERE trade_date < ?", (cutoff,))
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
        with _connect() as conn:
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
            with _connect() as conn:
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
        with _connect() as conn:
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
    try:
        latest, count, ratio = _check_coverage(
            conn,
            codes,
            expected_date=target,
        )
    finally:
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


def fetch_quotes_frame(code, start=None, end=None):
    """本地库按代码读取日线，返回标准列 DataFrame（过滤非法 OHLCV）。"""
    params: list = [str(code)]
    clause = "code = ?"
    if start is not None:
        clause += " AND trade_date >= ?"
        params.append(str(start))
    if end is not None:
        clause += " AND trade_date <= ?"
        params.append(str(end))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_QUOTE_COLUMNS)} FROM DailyQuote "
            f"WHERE {clause} ORDER BY trade_date",
            params,
        ).fetchall()
    return _sanitize_quote_frame(pd.DataFrame(rows, columns=_QUOTE_COLUMNS))


def fetch_quotes(code, start):
    """adata 拉取单只日线并清洗为标准列 DataFrame。

    返回 (frame, error)；供 watch_advice 等技术面取证作为末选数据源。
    """
    _code, rows, err = _fetch_one(code, start)
    if err is not None:
        return pd.DataFrame(columns=_QUOTE_COLUMNS), err
    frame = pd.DataFrame(rows, columns=_QUOTE_COLUMNS)
    return _sanitize_quote_frame(frame), None


def load_all(min_rows=60):
    """加载全部日线，返回 {code: DataFrame}，过滤不足 min_rows 的。"""
    conn = _conn()
    try:
        df = pd.read_sql("SELECT * FROM DailyQuote ORDER BY code, trade_date", conn)
    finally:
        conn.close()
    df = _sanitize_quote_frame(df)
    if df.empty:
        return {}
    return {
        code: group.reset_index(drop=True)
        for code, group in df.groupby("code")
        if len(group) >= min_rows
    }
