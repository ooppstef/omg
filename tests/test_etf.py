import unittest

import numpy as np
import pandas as pd

from stocks.etf import (
    STATE_DOWN,
    STATES,
    build_comment,
    build_reason_block,
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
        n = 120
        values = [1.0] * (n - 15) + list(np.linspace(1.0, 1.12, 15))
        close = _series(values)
        amount = _series([1e9] * (n - 8) + [2.0e9] * 8)
        bench = _series([1.0] * n)
        feat = compute_features(close, amount, bench)
        short, reasons = classify_horizon(feat, "short")
        self.assertIn(short, STATES)
        self.assertTrue(reasons)
        result = classify_etf(close, amount, bench)
        self.assertIn(result["short_state"], STATES)
        self.assertIn(result["mid_state"], STATES)
        self.assertTrue(result["short_reasons"])

    def test_classify_chop(self):
        n = 120
        rng = np.random.default_rng(42)
        noise = 1.0 + 0.01 * np.sin(np.linspace(0, 20, n)) + rng.normal(0, 0.002, n)
        close = _series(noise)
        amount = _series([1e9] * n)
        bench = _series(noise * 0.99)
        feat = compute_features(close, amount, bench)
        state, reasons = classify_horizon(feat, "short")
        self.assertEqual(state, "无效震荡")
        self.assertTrue(any("缠绕" in r or "震荡" in r or "平缓" in r for r in reasons))

    def test_classify_weaken_near_high(self):
        n = 120
        up = list(np.linspace(1.0, 1.4, n - 8))
        down = list(np.linspace(1.4, 1.32, 8))
        close = _series(up + down)
        amount = _series([1e9] * (n - 5) + [2.2e9] * 5)
        bench = _series(np.linspace(1.0, 1.15, n))
        feat = compute_features(close, amount, bench)
        state, reasons = classify_horizon(feat, "short")
        self.assertIn(state, ("见顶迹象", "空头趋势", "无效震荡"))
        self.assertTrue(reasons)

    def test_zero_range_position_remains_real_low(self):
        features = {
            "ok": True,
            "ma20_slope": -0.01,
            "vol_ratio": 1.0,
            "range_pos_60": 0.0,
            "ma_spread": 0.1,
            "vol_20": 0.02,
            "vol_60": 0.02,
            "excess_10": -0.05,
            "ret_10": -0.10,
            "ret_5": -0.05,
            "above_ma20": False,
            "ma_bull": False,
            "ma_bear": True,
        }
        state, _ = classify_horizon(features, "short")
        self.assertEqual(state, STATE_DOWN)

    def test_build_comment_and_reason_block(self):
        row = {
            "short_state": "启动趋势",
            "mid_state": "无效震荡",
            "change_pct": 2.5,
            "short_reasons": ["短线启动：站上MA20", "量比1.50"],
            "mid_reasons": ["中线均线缠绕"],
            "features": {
                "ret_10": 0.06,
                "excess_10": 0.03,
                "vol_ratio": 1.5,
                "range_pos_60": 0.7,
                "ma20_slope": 0.01,
            },
        }
        text = build_comment(row)
        self.assertIn("站上MA20", text)
        block = build_reason_block(row)
        self.assertIn("短线理由", block)
        self.assertIn("中线理由", block)


if __name__ == "__main__":
    unittest.main()
