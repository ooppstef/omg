import unittest

import numpy as np
import pandas as pd

from stocks.etf import (
    STATES,
    build_comment,
    classify_etf,
    classify_horizon,
    compute_features,
    load_universe,
)


def _series(values, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


class TestUniverse(unittest.TestCase):
    def test_load_universe_has_reps(self):
        rows = load_universe()
        self.assertGreaterEqual(len(rows), 50)
        self.assertIn("code", rows[0])
        self.assertIn("theme", rows[0])
        codes = [r["code"] for r in rows]
        self.assertEqual(len(codes), len(set(codes)))


class TestFeaturesAndState(unittest.TestCase):
    def test_compute_features_basic(self):
        n = 120
        close = _series(np.linspace(1.0, 1.5, n))
        amount = _series([1e9] * n)
        bench = _series(np.linspace(1.0, 1.1, n))
        feat = compute_features(close, amount, bench)
        self.assertIn("ret_10", feat)
        self.assertIn("excess_10", feat)
        self.assertIn("ma20", feat)
        self.assertIn("vol_ratio", feat)
        self.assertTrue(np.isfinite(feat["ret_10"]))

    def test_classify_uptrend_start(self):
        # 长期横盘后近端抬升放量
        n = 120
        values = [1.0] * (n - 15) + list(np.linspace(1.0, 1.12, 15))
        close = _series(values)
        amount = _series([1e9] * (n - 8) + [2.0e9] * 8)
        bench = _series([1.0] * n)
        feat = compute_features(close, amount, bench)
        short = classify_horizon(feat, "short")
        self.assertIn(short, STATES)
        result = classify_etf(close, amount, bench)
        self.assertIn(result["short_state"], STATES)
        self.assertIn(result["mid_state"], STATES)

    def test_classify_chop(self):
        n = 120
        rng = np.random.default_rng(42)
        noise = 1.0 + 0.01 * np.sin(np.linspace(0, 20, n)) + rng.normal(0, 0.002, n)
        close = _series(noise)
        amount = _series([1e9] * n)
        bench = _series(noise * 0.99)
        feat = compute_features(close, amount, bench)
        state = classify_horizon(feat, "short")
        self.assertEqual(state, "无效震荡")

    def test_classify_weaken_near_high(self):
        n = 120
        up = list(np.linspace(1.0, 1.4, n - 8))
        # 高位回落
        down = list(np.linspace(1.4, 1.32, 8))
        close = _series(up + down)
        amount = _series([1e9] * (n - 5) + [2.2e9] * 5)
        bench = _series(np.linspace(1.0, 1.15, n))
        feat = compute_features(close, amount, bench)
        state = classify_horizon(feat, "short")
        self.assertIn(state, ("走弱见顶", "弱势续跌", "无效震荡"))

    def test_build_comment_contains_keywords(self):
        row = {
            "short_state": "启动趋势",
            "mid_state": "无效震荡",
            "change_pct": 2.5,
            "features": {
                "ret_10": 0.06,
                "excess_10": 0.03,
                "vol_ratio": 1.5,
                "range_pos_60": 0.7,
                "ma20_slope": 0.01,
            },
        }
        text = build_comment(row)
        self.assertTrue(any(k in text for k in ("放量", "超额", "趋势", "均线")))


if __name__ == "__main__":
    unittest.main()
