import unittest
from unittest.mock import patch

import pandas as pd

from stocks.crowd import (
    MIN_HISTORY_DAYS,
    SHARE_SMOOTH_DAYS,
    build_share_series,
    compute_board_crowding,
    compute_share,
    crowding_from_shares,
    history_start_date,
    percentile_rank,
    rank_crowding_rows,
    rolling_mean,
)


class TestCrowdMath(unittest.TestCase):
    def test_compute_share_basic(self):
        self.assertAlmostEqual(compute_share(100.0, 1000.0), 0.1)
        self.assertIsNone(compute_share(100.0, 0.0))
        self.assertIsNone(compute_share(float("nan"), 1000.0))

    def test_rolling_mean_window(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = rolling_mean(s, 3)
        self.assertAlmostEqual(out.iloc[-1], 4.0)
        self.assertTrue(pd.isna(out.iloc[0]))

    def test_percentile_rank(self):
        hist = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        self.assertAlmostEqual(percentile_rank(0.04, hist), 60.0)
        self.assertIsNone(percentile_rank(0.04, pd.Series(dtype=float)))

    def test_crowding_from_shares_needs_history(self):
        short = pd.Series([0.01] * 50)
        short.index = pd.date_range("2024-01-01", periods=50, freq="B")
        result = crowding_from_shares(short)
        self.assertFalse(result["available"])

    def test_crowding_from_shares_percentile(self):
        # 注意：分位基于 share_5d。末尾须连续抬高 ≥5 日，否则会被 5 日均值稀释。
        # 需要 ≥ MIN_HISTORY_DAYS 条历史（不含当日），故总长度至少 +1+5。
        n = MIN_HISTORY_DAYS + 1 + 50
        values = [0.01] * (n - 5) + [0.05] * 5
        s = pd.Series(values, index=pd.date_range("2020-01-01", periods=n, freq="B"))
        result = crowding_from_shares(s)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["share_5d"], 0.05)
        self.assertGreaterEqual(result["crowd_pct"], 90.0)
        self.assertGreaterEqual(result["history_days"], MIN_HISTORY_DAYS)
        self.assertIn("share", result)

    def test_crowding_from_shares_boundary_history(self):
        # 满 5 日平滑后，非空 share_5d 恰为 MIN_HISTORY_DAYS（含当日）→ 历史不足
        n = MIN_HISTORY_DAYS + SHARE_SMOOTH_DAYS - 1
        values = [0.01] * n
        s = pd.Series(values, index=pd.date_range("2020-01-01", periods=n, freq="B"))
        result = crowding_from_shares(s)
        self.assertFalse(result["available"])


class TestShareSeries(unittest.TestCase):
    def test_build_share_series_aligns_on_intersection(self):
        concept = pd.Series(
            [100.0, 200.0, 300.0],
            index=["2024-01-02", "2024-01-03", "2024-01-04"],
        )
        market = pd.Series(
            [1000.0, 0.0, 1500.0],
            index=["2024-01-02", "2024-01-03", "2024-01-04"],
        )
        share = build_share_series(concept, market)
        self.assertEqual(list(share.index.astype(str)), ["2024-01-02", "2024-01-04"])
        self.assertAlmostEqual(share.iloc[0], 0.1)
        self.assertAlmostEqual(share.iloc[1], 0.2)

    def test_history_start_date_format(self):
        d = history_start_date(10)
        self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$")

    @patch("stocks.crowd.crowding_from_shares")
    @patch("stocks.crowd.fetch_board_frame")
    def test_board_result_uses_last_aligned_trade_date(self, fetch_frame, crowding):
        fetch_frame.return_value = pd.DataFrame(
            {
                "trade_date": ["2026-07-16", "2026-07-17"],
                "amount": [100.0, 200.0],
                "change_pct": [1.0, 2.0],
            }
        )
        crowding.return_value = {
            "available": True,
            "share": 0.1,
            "share_5d": 0.1,
            "crowd_pct": 80.0,
            "history_days": 120,
        }
        market = pd.Series([1000.0], index=["2026-07-16"])

        result = compute_board_crowding("BK001", "测试", market)

        self.assertEqual(result["trade_date"], "2026-07-16")
        self.assertEqual(result["change_pct"], 1.0)


class TestRank(unittest.TestCase):
    def test_rank_sorts_by_crowd_pct_desc(self):
        rows = [
            {"concept_name": "A", "available": True, "crowd_pct": 50.0, "share_5d": 0.01},
            {"concept_name": "B", "available": True, "crowd_pct": 90.0, "share_5d": 0.02},
            {"concept_name": "C", "available": False, "crowd_pct": None, "share_5d": 0.03},
        ]
        ranked = rank_crowding_rows(rows, top=10)
        self.assertEqual([r["concept_name"] for r in ranked], ["B", "A"])


if __name__ == "__main__":
    unittest.main()
