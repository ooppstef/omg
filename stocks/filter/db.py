"""日线数据持久化。"""

import datetime
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import adata
import pandas as pd
from stocks.db import dir_path

# ---------- 配置 ----------
INIT_DAYS = 200  # 初始化拉取交易日数（约10个月）
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


def _check_coverage(conn, expected, table="DailyQuote"):
    """返回 (最新日期, 覆盖数, 覆盖率)。"""
    row = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
    latest = row[0] if row else None
    if latest is None:
        raise RuntimeError("未拉取到任何日线数据")
    count = conn.execute(
        f"SELECT COUNT(DISTINCT code) FROM {table} WHERE trade_date = ?",
        (latest,),
    ).fetchone()[0]
    ratio = count / expected if expected else 0
    return latest, count, ratio


def _require_coverage(conn, expected, table="DailyQuote", gate=COVERAGE_GATE):
    """覆盖率不达标则抛异常。"""
    latest, count, ratio = _check_coverage(conn, expected, table)
    if ratio < gate:
        raise RuntimeError(f"覆盖率不足: {latest} 仅 {count}/{expected} ({ratio:.1%})")
    return latest, count, ratio


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


def full_init(progress=True):
    """全量初始化，写临时表后原子替换。"""
    _init_table()
    codes = _all_codes()
    if not codes:
        print("获取股票列表失败")
        return

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
            conn, total, "DailyQuote_tmp", INIT_COVERAGE_GATE
        )
        if progress:
            print(f"  最新交易日 {latest} 覆盖 {count}/{total} ({ratio:.1%})")

        conn.execute("DROP TABLE IF EXISTS DailyQuote")
        conn.execute("ALTER TABLE DailyQuote_tmp RENAME TO DailyQuote")
        conn.commit()
    except Exception:
        conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
        conn.commit()
        raise
    finally:
        conn.close()

    if progress:
        _print_failures(fail, samples, total)
        print(f"全量初始化完成 (成功 {ok}/{total})")


# ---------- 增量更新 ----------


def incremental_update(progress=True):
    """增量更新，写临时表后原子替换。"""
    last = _latest_date()
    if last is None:
        full_init(progress)
        return

    codes = _all_codes()
    if not codes:
        print("获取股票列表失败")
        return

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
        latest, count, ratio = _require_coverage(conn, total, "DailyQuote_tmp")
        # 清理过期数据
        cutoff = _cutoff_date()
        conn.execute(f"DELETE FROM DailyQuote_tmp WHERE trade_date < ?", (cutoff,))
        conn.commit()
        if progress:
            print(f"  最新交易日 {latest} 覆盖 {count}/{total} ({ratio:.1%})")

        conn.execute("DROP TABLE IF EXISTS DailyQuote")
        conn.execute("ALTER TABLE DailyQuote_tmp RENAME TO DailyQuote")
        conn.commit()
    except Exception:
        conn.execute("DROP TABLE IF EXISTS DailyQuote_tmp")
        conn.commit()
        raise
    finally:
        conn.close()

    if progress:
        _print_failures(fail, samples, total)
        print(f"增量更新完成 (成功 {ok}/{total})")


# ---------- 对外接口 ----------


def _check_stamp_path():
    return dir_path().replace(".db", ".last_check")


def ensure_data(progress=True):
    """确保数据可用且最新。每天只检查一次，检查成功后记录时间戳。"""
    import os

    stamp = _check_stamp_path()
    today = datetime.date.today().isoformat()
    if os.path.exists(stamp):
        with open(stamp) as f:
            if f.read().strip() == today:
                if progress:
                    print("今日已检查，跳过")
                return

    _init_table()

    if _latest_date() is None:
        full_init(progress)
        with open(stamp, "w") as f:
            f.write(today)
        return

    target = _target_date()
    if target is None:
        raise RuntimeError("无法获取目标交易日（网络异常），请检查网络后重试")

    if _latest_date() < target:
        if progress:
            print(f"数据落后，目标交易日 {target}，开始增量更新...")
        incremental_update(progress)
        with open(stamp, "w") as f:
            f.write(today)
        return

    # 日期最新，检查覆盖率（分母用全市场应有数，防止残缺库自我验证100%）
    codes = _all_codes()
    expected = len(codes)
    if expected == 0:
        raise RuntimeError("获取股票列表失败，无法验证数据完整性")
    conn = _conn()
    latest, count, ratio = _check_coverage(conn, expected)
    conn.close()

    if ratio < COVERAGE_GATE:
        if progress:
            print(
                f"覆盖 {count}/{expected} ({ratio:.1%})，低于 {COVERAGE_GATE:.0%}，增量补齐"
            )
        incremental_update(progress)
    else:
        if progress:
            print("日线数据已是最新")

    with open(stamp, "w") as f:
        f.write(today)


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
