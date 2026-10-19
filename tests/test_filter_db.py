import datetime
from contextlib import contextmanager
import sqlite3
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stocks.filter import db


@contextmanager
def _open(database):
    """测试用连接：事务语义同 `with conn:`，退出时 close。

    `with sqlite3.connect(...) as conn:` 不关闭连接，连接进引用环只靠
    循环 GC 回收，Windows 上句柄不释放会导致临时库文件删不掉。
    """
    conn = sqlite3.connect(database)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class TestCoverage(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(db.DDL)
        self.conn.execute(
            "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("000001", "2026-07-16", 1, 1, 1, 1, 1, 1, 0, 0),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_target_date_coverage_rejects_complete_but_stale_data(self):
        with self.assertRaisesRegex(RuntimeError, "目标交易日"):
            db._require_coverage(
                self.conn,
                expected=1,
                expected_date="2026-07-17",
            )

    def test_coverage_counts_expected_codes_not_unrelated_rows(self):
        self.conn.execute(
            "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("999999", "2026-07-16", 1, 1, 1, 1, 1, 1, 0, 0),
        )
        self.conn.commit()

        _, count, ratio = db._check_coverage(
            self.conn,
            expected=["000001", "000002"],
            expected_date="2026-07-16",
        )

        self.assertEqual(count, 1)
        self.assertEqual(ratio, 0.5)

    def test_coverage_does_not_count_invalid_ohlcv_rows(self):
        self.conn.execute(
            "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("000002", "2026-07-16", 0, 0, 0, 0, 0, 0, 0, 0),
        )
        self.conn.commit()

        _, count, ratio = db._check_coverage(
            self.conn,
            expected=["000001", "000002"],
            expected_date="2026-07-16",
        )

        self.assertEqual(count, 1)
        self.assertEqual(ratio, 0.5)

    def test_coverage_rejects_unknown_table_name(self):
        with self.assertRaisesRegex(ValueError, "表名"):
            db._check_coverage(
                self.conn,
                expected=1,
                table="DailyQuote WHERE 1 = 1",
            )

    def test_history_coverage_requires_minimum_rows_per_expected_code(self):
        rows = [
            (
                "000001",
                f"2026-05-{day:02d}",
                1,
                1,
                1,
                1,
                1,
                1,
                0,
                0,
            )
            for day in range(1, 21)
        ]
        self.conn.executemany("INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()

        count, ratio = db._check_history_coverage(
            self.conn,
            expected=["000001", "000002"],
            min_rows=20,
        )

        self.assertEqual(count, 1)
        self.assertEqual(ratio, 0.5)

    def test_stamp_matches_verified_target_not_wall_clock_date(self):
        self.assertTrue(
            db._stamp_matches_target(
                "2026-07-16",
                target_date="2026-07-16",
                latest_date="2026-07-16",
            )
        )
        self.assertFalse(
            db._stamp_matches_target(
                "2026-07-16",
                target_date="2026-07-17",
                latest_date="2026-07-16",
            )
        )

    def test_atomic_table_swap_rolls_back_when_replacement_is_missing(self):
        self.conn.execute(
            "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("000002", "2026-07-16", 1, 1, 1, 1, 1, 1, 0, 0),
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.OperationalError):
            db._replace_table_atomically(
                self.conn,
                source_table="DailyQuote_tmp",
                target_table="DailyQuote",
            )

        count = self.conn.execute("SELECT COUNT(*) FROM DailyQuote").fetchone()[0]
        self.assertEqual(count, 2)


class TestBatchFetch(unittest.TestCase):
    def test_adjusted_overlap_refreshes_the_whole_stock_history(self):
        def quote(day, price):
            return ("600001", day, price, price, price, price, 100, 1000, 0, 1)

        old = [quote(f"2026-07-{day}", 10.0) for day in (14, 15, 16)]
        updated = [quote(f"2026-07-{day}", 5.0) for day in (14, 15, 16, 17)]
        for refresh in (updated, [], updated[-2:]):
            with self.subTest(refresh_rows=len(refresh)):
                conn = sqlite3.connect(":memory:")
                try:
                    conn.execute(db.DDL)
                    conn.executemany("INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)", old)
                    with patch.object(db, "_fetch_one", side_effect=[
                        ("600001", updated[-2:], None), ("600001", refresh, None)
                    ]) as fetch:
                        ok, fail, _ = db._batch_fetch(
                            ["600001"], "2026-07-16", conn, "DailyQuote", "更新", False
                        )
                    actual = conn.execute("SELECT * FROM DailyQuote ORDER BY trade_date").fetchall()
                    if refresh == updated:
                        self.assertEqual(actual, updated)
                        self.assertEqual((ok, fail), (1, 0))
                    else:
                        self.assertEqual(actual, old)
                        self.assertEqual((ok, fail), (0, 1))
                    self.assertEqual(fetch.call_args_list, [
                        unittest.mock.call("600001", "2026-07-16"),
                        unittest.mock.call("600001", "2026-07-14"),
                    ])
                finally:
                    conn.close()

    def test_incremental_fetch_overlaps_each_stocks_own_latest_date(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(db.DDL)
            row = ("600001", "2026-07-14", 10, 10, 10, 10, 100, 1000, 0, 1)
            conn.execute("INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            with patch.object(db, "_fetch_one", return_value=("600001", [row], None)) as fetch:
                db._batch_fetch(["600001"], "2026-07-16", conn, "DailyQuote", "更新", False)
            fetch.assert_called_once_with("600001", "2026-07-14")
        finally:
            conn.close()

    @patch("stocks.filter.db.adata.stock.market.get_market")
    def test_fetch_one_rejects_invalid_ohlcv_rows(self, get_market):
        get_market.return_value = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-16",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "change_pct": 2.0,
                    "turnover_ratio": 1.0,
                },
                {
                    "trade_date": "2026-07-17",
                    "open": 10.0,
                    "high": 9.9,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "change_pct": 2.0,
                    "turnover_ratio": 1.0,
                },
                {
                    "trade_date": "2026-07-18",
                    "open": None,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "change_pct": 2.0,
                    "turnover_ratio": 1.0,
                },
                {
                    "trade_date": "2026-07-19",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 0.0,
                    "amount": 0.0,
                    "change_pct": 2.0,
                    "turnover_ratio": 0.0,
                },
            ]
        )

        code, rows, error = db._fetch_one("600000", "2026-01-01")

        self.assertEqual(code, "600000")
        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "2026-07-16")

    @patch("stocks.filter.db.adata.stock.market.get_market")
    def test_fetch_one_prefers_existing_turnover_column(self, get_market):
        get_market.return_value = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-16",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "change_pct": 2.0,
                    "turnover": 1.5,
                    "turnover_ratio": 99.0,
                }
            ]
        )

        _, rows, error = db._fetch_one("600000", "2026-01-01")

        self.assertIsNone(error)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][-1], 1.5)

    @patch("stocks.filter.db.adata.stock.market.get_market")
    def test_fetch_one_keeps_valid_ohlcv_when_auxiliary_fields_are_missing(
        self,
        get_market,
    ):
        get_market.return_value = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-15",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.0,
                    "volume": 100.0,
                    "amount": None,
                    "change_pct": 0.0,
                    "turnover_ratio": None,
                },
                {
                    "trade_date": "2026-07-16",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.9,
                    "close": 10.2,
                    "volume": 100.0,
                    "amount": None,
                    "change_pct": None,
                    "turnover_ratio": None,
                },
            ]
        )

        _, rows, error = db._fetch_one("600000", "2026-01-01")

        self.assertIsNone(error)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[-1][7])
        self.assertAlmostEqual(rows[-1][8], 2.0)
        self.assertIsNone(rows[-1][9])

    def test_sanitize_rejects_impossible_calendar_date(self):
        frame = pd.DataFrame(
            [
                {
                    "code": "600000",
                    "trade_date": "2026-02-30",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "change_pct": 2.0,
                    "turnover": 1.5,
                }
            ]
        )

        self.assertTrue(db._sanitize_quote_frame(frame).empty)

    def test_load_all_filters_invalid_rows_before_minimum_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "quotes.db"
            with _open(database) as conn:
                conn.execute(db.DDL)
                conn.executemany(
                    "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            "600000",
                            "2026-07-16",
                            10.0,
                            10.5,
                            9.8,
                            10.2,
                            100.0,
                            1000.0,
                            2.0,
                            1.0,
                        ),
                        (
                            "600000",
                            "2026-07-17",
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                        ),
                    ],
                )

            with patch(
                "stocks.filter.db._conn",
                side_effect=lambda: sqlite3.connect(database),
            ):
                loaded = db.load_all(min_rows=2)

        self.assertNotIn("600000", loaded)

    def test_ensure_data_serializes_the_complete_check_and_repair_flow(self):
        self.assertTrue(
            hasattr(db.ensure_data, "__wrapped__"),
            "ensure_data 必须持更新锁完成检查、修复与写入时间戳",
        )

    @patch("stocks.filter.db._fetch_one", return_value=("000001", [], None))
    def test_empty_response_counts_as_failure(self, _fetch):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(db.DDL)
            ok, fail, samples = db._batch_fetch(
                ["000001"],
                "2026-07-17",
                conn,
                "DailyQuote",
                "test",
                progress=False,
            )
        finally:
            conn.close()

        self.assertEqual(ok, 0)
        self.assertEqual(fail, 1)
        self.assertTrue(samples)

    def test_update_lock_is_stored_in_user_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                with db._update_lock():
                    self.assertEqual(
                        Path(db._update_lock_path()).parent,
                        Path(tmp),
                    )
                    self.assertTrue(Path(db._update_lock_path()).exists())

    def test_same_date_with_low_coverage_runs_incremental_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "quotes.db"

            def connect():
                return sqlite3.connect(database)

            with _open(database) as conn:
                conn.execute(db.DDL)
                conn.execute(
                    "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("000001", "2026-07-17", 1, 1, 1, 1, 1, 1, 0, 0),
                )

            def fill_tmp(_codes, _start, conn, table, _label, _progress):
                conn.executemany(
                    f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [
                        ("000001", "2026-07-17", 1, 1, 1, 1, 1, 1, 0, 0),
                        ("000002", "2026-07-17", 1, 1, 1, 1, 1, 1, 0, 0),
                    ],
                )
                conn.commit()
                return 2, 0, []

            with (
                patch("stocks.filter.db._conn", side_effect=connect),
                patch("stocks.filter.db._all_codes", return_value=["000001", "000002"]),
                patch("stocks.filter.db._batch_fetch", side_effect=fill_tmp) as batch,
                patch("stocks.filter.db._cutoff_date", return_value="2026-01-01"),
                patch("stocks.filter.db._update_lock"),
            ):
                result = db.incremental_update(
                    progress=False,
                    target_date="2026-07-17",
                )

            self.assertTrue(result)
            batch.assert_called_once()

    def test_matching_stamp_does_not_hide_incomplete_target_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "quotes.db"
            stamp = Path(tmp) / "stamp"
            stamp.write_text("2026-07-17", encoding="utf-8")

            def connect():
                return sqlite3.connect(database)

            with _open(database) as conn:
                conn.execute(db.DDL)
                conn.execute(
                    "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("000001", "2026-07-17", 1, 1, 1, 1, 1, 1, 0, 0),
                )

            def fill(_progress=True, target_date=None, codes=None):
                with _open(database) as conn:
                    conn.execute(
                        "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
                        ("000002", target_date, 1, 1, 1, 1, 1, 1, 0, 0),
                    )
                return True

            with (
                patch("stocks.filter.db._conn", side_effect=connect),
                patch("stocks.filter.db._target_date", return_value="2026-07-17"),
                patch("stocks.filter.db._all_codes", return_value=["000001", "000002"]),
                patch("stocks.filter.db._check_stamp_path", return_value=str(stamp)),
                patch("stocks.filter.db._check_history_coverage", return_value=(2, 1.0)),
                patch("stocks.filter.db.incremental_update", side_effect=fill) as update,
            ):
                db.ensure_data(progress=False)

            update.assert_called_once()

    def test_empty_database_initialization_must_reach_requested_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "quotes.db"

            def connect():
                return sqlite3.connect(database)

            with _open(database) as conn:
                conn.execute(db.DDL)

            with (
                patch("stocks.filter.db._conn", side_effect=connect),
                patch("stocks.filter.db._latest_date", return_value=None),
                patch("stocks.filter.db.full_init", return_value=True),
                patch("stocks.filter.db._update_lock"),
            ):
                with self.assertRaisesRegex(RuntimeError, "目标交易日"):
                    db.incremental_update(
                        progress=False,
                        target_date="2026-07-17",
                        codes=["000001"],
                    )

    def test_sparse_history_triggers_full_reinitialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "quotes.db"
            stamp = Path(tmp) / "stamp"

            def connect():
                return sqlite3.connect(database)

            with _open(database) as conn:
                conn.execute(db.DDL)
                conn.executemany(
                    "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [
                        (code, "2026-07-17", 1, 1, 1, 1, 1, 1, 0, 0)
                        for code in ("000001", "000002")
                    ],
                )

            def fill_history(_progress=True, codes=None):
                start = datetime.date(2026, 5, 19)
                rows = []
                for code in codes:
                    for offset in range(60):
                        trade_date = (start + datetime.timedelta(days=offset)).isoformat()
                        rows.append(
                            (code, trade_date, 1, 1, 1, 1, 1, 1, 0, 0)
                        )
                with _open(database) as conn:
                    conn.execute("DELETE FROM DailyQuote")
                    conn.executemany(
                        "INSERT INTO DailyQuote VALUES (?,?,?,?,?,?,?,?,?,?)",
                        rows,
                    )
                return True

            with (
                patch("stocks.filter.db._conn", side_effect=connect),
                patch("stocks.filter.db._target_date", return_value="2026-07-17"),
                patch("stocks.filter.db._all_codes", return_value=["000001", "000002"]),
                patch("stocks.filter.db._check_stamp_path", return_value=str(stamp)),
                patch("stocks.filter.db.full_init", side_effect=fill_history) as full,
            ):
                db.ensure_data(progress=False)

            full.assert_called_once()


class TestExchangePatch(unittest.TestCase):
    def test_safe_exchange_patch_restores_both_adata_bindings_on_error(self):
        import importlib

        import adata.common.utils.code_utils as code_utils

        stock_code = importlib.import_module("adata.stock.info.stock_code")

        original_utils = code_utils.get_exchange_by_stock_code
        original_stock_code = stock_code.get_exchange_by_stock_code

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with db._safe_exchange_patch():
                self.assertIsNot(
                    code_utils.get_exchange_by_stock_code,
                    original_utils,
                )
                self.assertEqual(code_utils.get_exchange_by_stock_code("810000"), "")
                self.assertEqual(stock_code.get_exchange_by_stock_code("810000"), "")
                raise RuntimeError("boom")

        self.assertIs(code_utils.get_exchange_by_stock_code, original_utils)
        self.assertIs(stock_code.get_exchange_by_stock_code, original_stock_code)


if __name__ == "__main__":
    unittest.main()
