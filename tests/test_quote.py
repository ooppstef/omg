"""stocks.quote 基础清洗原语单测。"""

import math
import unittest

import pandas as pd

from stocks.quote import clean_numeric, normalize_date_col, parse_date_series


class TestParseDateSeries(unittest.TestCase):
    def test_parses_iso_date(self):
        # 契约：单格式 ISO 日期可靠解析；混合格式推断依赖 dateutil，不在契约内
        out = parse_date_series(pd.Series(["2026-07-21", "2026-07-22"]))
        self.assertEqual(out.dt.strftime("%Y-%m-%d").tolist(), ["2026-07-21", "2026-07-22"])

    def test_mixed_date_and_timestamp_formats(self):
        out = parse_date_series(
            pd.Series(["2026-07-21", "2026-07-22 15:30:00"])
        )
        self.assertEqual(
            out.dt.strftime("%Y-%m-%d").tolist(),
            ["2026-07-21", "2026-07-22"],
        )

    def test_invalid_dates_become_nat(self):
        out = parse_date_series(pd.Series(["not-a-date", "2026-07-21"]))
        self.assertTrue(pd.isna(out.iloc[0]))
        self.assertFalse(pd.isna(out.iloc[1]))

    def test_empty_series(self):
        self.assertTrue(parse_date_series(pd.Series(dtype=str)).empty)


class TestCleanNumeric(unittest.TestCase):
    def test_coerces_and_cleans_inf(self):
        out = clean_numeric(pd.Series([1.5, math.inf, -math.inf, "3", "bad"]))
        self.assertEqual(out.iloc[0], 1.5)
        self.assertTrue(pd.isna(out.iloc[1]))
        self.assertTrue(pd.isna(out.iloc[2]))
        self.assertEqual(out.iloc[3], 3.0)
        self.assertTrue(pd.isna(out.iloc[4]))

    def test_nan_preserved_as_na(self):
        out = clean_numeric(pd.Series([float("nan"), 1.0]))
        self.assertTrue(pd.isna(out.iloc[0]))
        self.assertEqual(out.iloc[1], 1.0)

    def test_empty_series(self):
        self.assertTrue(clean_numeric(pd.Series(dtype=float)).empty)


class TestNormalizeDateCol(unittest.TestCase):
    def test_normalizes_and_drops_invalid_rows(self):
        frame = pd.DataFrame({"trade_date": ["2026-07-21", "bad", "2026-07-22"], "v": [1, 2, 3]})
        out = normalize_date_col(frame)
        self.assertEqual(out["trade_date"].tolist(), ["2026-07-21", "2026-07-22"])
        self.assertEqual(out["v"].tolist(), [1, 3])

    def test_all_invalid_returns_empty(self):
        frame = pd.DataFrame({"trade_date": ["bad", "worse"], "v": [1, 2]})
        self.assertTrue(normalize_date_col(frame).empty)

    def test_none_and_missing_col(self):
        self.assertIsNone(normalize_date_col(None))
        frame = pd.DataFrame({"other": [1]})
        self.assertIs(frame, normalize_date_col(frame))

    def test_does_not_mutate_input(self):
        frame = pd.DataFrame({"trade_date": ["2026-07-21"], "v": [1]})
        normalize_date_col(frame)
        self.assertEqual(frame["trade_date"].tolist(), ["2026-07-21"])


if __name__ == "__main__":
    unittest.main()
