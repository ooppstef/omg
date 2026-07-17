import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stocks import copy_watchlist, db, list_parser, paths


class TestLocalDataPaths(unittest.TestCase):
    def test_runtime_files_use_configurable_user_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                self.assertEqual(Path(db.dir_path()).parent, Path(tmp))
                self.assertEqual(list_parser.my_list_path().parent, Path(tmp))

    def test_empty_or_relative_xdg_path_uses_absolute_default(self):
        with (
            patch("sys.platform", "linux"),
            patch.dict(os.environ, {"XDG_DATA_HOME": "", "OMG_DATA_DIR": ""}),
        ):
            self.assertTrue(paths.user_data_dir().is_absolute())
        with (
            patch("sys.platform", "linux"),
            patch.dict(
                os.environ,
                {"XDG_DATA_HOME": "relative-data", "OMG_DATA_DIR": ""},
            ),
        ):
            self.assertNotIn("relative-data", str(paths.user_data_dir()))

    def test_empty_data_dir_override_does_not_disable_legacy_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy.txt"
            destination_dir = Path(tmp) / "data"
            destination_dir.mkdir()
            legacy.write_text("legacy", encoding="utf-8")
            with (
                patch.dict(os.environ, {"OMG_DATA_DIR": ""}),
                patch("stocks.paths.user_data_dir", return_value=destination_dir),
            ):
                target = paths.data_file("migrated.txt", legacy_path=legacy)

            self.assertEqual(target.read_text(encoding="utf-8"), "legacy")

    def test_missing_watchlist_is_initialized_with_supported_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                sections = list_parser.parse_my_list()

                self.assertEqual(sections, {"自选": [], "查看": []})
                self.assertTrue(list_parser.my_list_path().exists())

    def test_base_queries_are_safe_before_explicit_data_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                self.assertIsNone(db.search_base_by_code("000001"))

    def test_base_query_recovers_if_initialized_database_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                db.init_table()
                Path(db.dir_path()).unlink()

                self.assertIsNone(db.search_base_by_code("000001"))

    def test_base_batch_is_idempotent_and_updates_existing_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                db.init_table()
                db.insert_base_batch(
                    [("000001", "旧名称", "JMC", "SZ")]
                )
                db.insert_base_batch(
                    [("000001", "新名称", "XMC", "SZ")]
                )

                stock = db.search_base_by_code("000001")
                self.assertEqual(stock.name, "新名称")
                self.assertEqual(stock.short, "XMC")

    @patch("stocks.db.adata.stock.info.all_code", return_value=None)
    def test_base_data_handles_empty_provider_response(self, _all_code):
        self.assertEqual(db.get_base_data(), [])

    @patch("stocks.db.get_base_data")
    def test_partial_base_data_is_rejected_before_persistence(self, get_data):
        get_data.return_value = [
            ("000001", "平安银行", "PAYH", "SZ"),
            ("600000", "浦发银行", "PFYH", "SH"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                with self.assertRaisesRegex(RuntimeError, "不完整"):
                    db.refresh_base_data()

                self.assertIsNone(db.search_base_by_code("000001"))

    def test_failed_database_migration_does_not_publish_empty_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy.db"
            target = Path(tmp) / "target.db"
            legacy.write_bytes(b"not a sqlite database")

            with self.assertRaises(Exception):
                paths._copy_legacy_file(legacy, target)

            self.assertFalse(target.exists())

    def test_database_migration_handles_uri_special_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy?#.db"
            target = Path(tmp) / "target.db"
            with sqlite3.connect(legacy) as conn:
                conn.execute("CREATE TABLE sample(value TEXT)")
                conn.execute("INSERT INTO sample VALUES ('ok')")

            paths._copy_legacy_file(legacy, target)

            with sqlite3.connect(target) as conn:
                value = conn.execute("SELECT value FROM sample").fetchone()[0]
            self.assertEqual(value, "ok")

    def test_temporary_paths_are_unique_within_one_process(self):
        target = Path("/tmp/watchlist.txt")
        self.assertNotEqual(
            paths.temporary_path_for(target),
            paths.temporary_path_for(target),
        )

    def test_watchlist_initializer_creates_complete_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OMG_DATA_DIR": tmp}):
                path = list_parser.initialize_my_list()

                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    list_parser.DEFAULT_LIST_CONTENT,
                )

    def test_copy_uses_initialized_user_watchlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.txt"
            with patch.dict(os.environ, {"OMG_DATA_DIR": str(Path(tmp) / "data")}):
                count = copy_watchlist.sync_watchlist(target_path=target)

            self.assertEqual(count, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "# 自选\n")

    def test_copy_rejects_same_source_and_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            watchlist = Path(tmp) / "my_list.txt"
            original = "# 自选\n000001\n\n# 查看\n000002\n"
            watchlist.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "不能相同"):
                copy_watchlist.sync_watchlist(
                    source_path=watchlist,
                    target_path=watchlist,
                )

            self.assertEqual(watchlist.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
