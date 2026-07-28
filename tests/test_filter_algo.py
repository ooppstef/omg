import unittest

import pandas as pd

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


if __name__ == "__main__":
    unittest.main()
