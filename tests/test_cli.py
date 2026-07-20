import io
import sys
import types
import unittest
from unittest.mock import patch

import main_controller
from stocks import main
from stocks.filter.algo import STRATEGIES
from stocks.filter.entry import _glossary_lines


class TestCli(unittest.TestCase):
    def test_init_command_is_available(self):
        self.assertIn("init", main_controller.COMMANDS)

    def test_notice_help_describes_secondary_watchlist_quotes(self):
        self.assertEqual(
            main_controller.COMMAND_META["notice"][1],
            "查看列表行情",
        )

    def test_non_tty_filter_selection_reads_one_line(self):
        with patch("sys.stdin", io.StringIO("1\n")):
            self.assertEqual(main._read_key(), "1")

    def test_windows_tty_filter_selection_uses_msvcrt(self):
        stdin = io.StringIO()
        stdin.isatty = lambda: True
        fake_msvcrt = types.SimpleNamespace(getwch=lambda: "1")
        with (
            patch("sys.platform", "win32"),
            patch("sys.stdin", stdin),
            patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
        ):
            self.assertEqual(main._read_key(), "1")

    def test_menu_only_exposes_implemented_strategies(self):
        self.assertEqual(list(STRATEGIES), ["1"])

    def test_filter_glossary_does_not_reference_a_missing_file(self):
        self.assertFalse(
            any("docs/filter-field-glossary.md" in line for line in _glossary_lines())
        )

    @patch("main_controller.run_command", return_value=False)
    def test_main_returns_nonzero_when_command_fails(self, _run):
        with patch("sys.argv", ["omg", "mood"]):
            self.assertEqual(main_controller.main(), 1)


if __name__ == "__main__":
    unittest.main()
