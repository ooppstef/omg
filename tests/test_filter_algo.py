import unittest
from unittest import mock

import numpy as np
import pandas as pd

from stocks.filter import algo, indicator
from stocks.filter.algo import _nan, _pullback_state


class TestPullbackIndexing(unittest.TestCase):
    def test_nonfinite_indicator_is_missing(self):
        self.assertTrue(_nan(float("inf")))
        self.assertTrue(_nan(float("-inf")))

    def test_shifted_index_does_not_change_pullback_result(self):
        rows = []
        for pos in range(20):
            before_peak = pos <= 14
            rows.append(
                {
                    "high": 12.0 if pos == 14 else 11.0,
                    "low": 10.0,
                    "close": 10.8,
                    "volume": 200.0 if before_peak else 100.0,
                    "ma20": 10.7,
                    "atr": 0.5,
                    "change_pct": 1.0 if before_peak else -0.5,
                    "rv20": 1.0,
                    "close_loc": 0.5,
                }
            )
        normal = pd.DataFrame(rows)
        shifted = normal.copy()
        shifted.index = range(100, 120)

        normal_state = _pullback_state(normal)
        shifted_state = _pullback_state(shifted)

        self.assertEqual(shifted_state["days_since_peak"], 5)
        self.assertEqual(shifted_state["days_since_peak"], normal_state["days_since_peak"])
        self.assertEqual(shifted_state["ok"], normal_state["ok"])


class TestTrendRules(unittest.TestCase):
    @staticmethod
    def _rally_frame():
        close = np.array([10.0] * 60 + list(np.linspace(11.0, 13.0, 12)) + [12.9, 13.5, 14.0])
        return pd.DataFrame({
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close, "volume": 1e6,
        })

    def test_clear_uptrend_measures_rally_from_trough_to_peak(self):
        frame = self._rally_frame()
        self.assertTrue(algo._has_clear_uptrend(indicator.compute_all(frame)))
        frame.loc[len(frame) - 3, "low"] = 9.8
        # 近低点到峰值只有 3 根 K 线，不能用整段 lookback 冒充 5 日上升段。
        self.assertFalse(algo._has_clear_uptrend(indicator.compute_all(frame)))

    def test_limit_up_filter_respects_stock_board(self):
        frame = indicator.compute_all(self._rally_frame())
        state = {"ok": True, "score": 10, "notes": [], "risks": [],
                 "drawdown": 0.1, "dryup_ratio": 0.8}
        with mock.patch.object(algo, "_pick_signal", return_value="小阳企稳"), mock.patch.object(
            algo, "_trend_state", return_value=state
        ), mock.patch.object(algo, "_pullback_state", return_value=state), mock.patch.object(
            algo, "_trigger_state", return_value=state
        ):
            for code, change, accepted in (
                ("600001", 10.0, False), ("000001", 10.0, False),
                ("300001", 10.0, True), ("688001", 10.0, True),
                ("689009", 10.0, True), ("300001", 20.0, False),
                ("688001", 20.0, False),
            ):
                with self.subTest(code=code, change=change):
                    frame.loc[frame.index[-3:-1], "change_pct"] = change
                    self.assertEqual(algo.trend_pullback(code, frame) is not None, accepted)


if __name__ == "__main__":
    unittest.main()
