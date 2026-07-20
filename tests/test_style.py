import unittest

import numpy as np
import pandas as pd

from stocks.style import (
    STATES,
    STATE_TECH,
    STATE_SMALL,
    STATE_LARGE,
    STATE_DIVIDEND,
    STATE_BALANCED,
    classify_style,
    compute_style_features,
)


def _idx(values, start="2025-01-02"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


class TestStyleFeatures(unittest.TestCase):
    def test_compute_relative(self):
        n = 80
        hs = _idx(np.linspace(1.0, 1.05, n))
        zz = _idx(np.linspace(1.0, 1.20, n))
        kc = _idx(np.linspace(1.0, 1.25, n))
        div = _idx(np.linspace(1.0, 1.02, n))
        feat = compute_style_features(
            {"hs300": hs, "zz1000": zz, "kc50": kc, "dividend": div}
        )
        self.assertTrue(feat["ok"])
        self.assertGreater(feat["ret5_zz1000"], feat["ret5_hs300"])
        self.assertGreater(feat["excess5_kc_hs"], 0)

    def test_short_history_not_ok(self):
        n = 10
        hs = _idx(np.linspace(1.0, 1.05, n))
        feat = compute_style_features(
            {"hs300": hs, "zz1000": hs, "kc50": hs, "dividend": hs}
        )
        self.assertFalse(feat["ok"])
        state, reasons = classify_style(feat)
        self.assertTrue(any("不足" in r and "非均衡" in r for r in reasons))
        self.assertEqual(state, STATE_BALANCED)  # 仅占位，打印层会显示数据不足


class TestClassifyStyle(unittest.TestCase):
    def test_tech_siphon(self):
        feat = {
            "ok": True,
            "ret5_hs300": 0.01,
            "ret5_zz1000": 0.02,
            "ret5_kc50": 0.08,
            "ret5_dividend": -0.01,
            "ret20_hs300": 0.02,
            "ret20_zz1000": 0.03,
            "ret20_kc50": 0.15,
            "ret20_dividend": 0.0,
            "excess5_kc_hs": 0.07,
            "excess20_kc_hs": 0.13,
            "excess5_zz_hs": 0.01,
            "excess20_zz_hs": 0.01,
            "excess5_div_hs": -0.02,
            "excess20_div_hs": -0.02,
        }
        state, reasons = classify_style(feat)
        self.assertEqual(state, STATE_TECH)
        self.assertTrue(reasons)

    def test_small_cap(self):
        feat = {
            "ok": True,
            "ret5_hs300": 0.0,
            "ret5_zz1000": 0.05,
            "ret5_kc50": 0.02,
            "ret5_dividend": 0.0,
            "ret20_hs300": 0.01,
            "ret20_zz1000": 0.10,
            "ret20_kc50": 0.03,
            "ret20_dividend": 0.01,
            "excess5_kc_hs": 0.02,
            "excess20_kc_hs": 0.02,
            "excess5_zz_hs": 0.05,
            "excess20_zz_hs": 0.09,
            "excess5_div_hs": 0.0,
            "excess20_div_hs": 0.0,
        }
        state, _ = classify_style(feat)
        self.assertEqual(state, STATE_SMALL)

    def test_large_cap(self):
        feat = {
            "ok": True,
            "ret5_hs300": 0.04,
            "ret5_zz1000": -0.01,
            "ret5_kc50": 0.0,
            "ret5_dividend": 0.02,
            "ret20_hs300": 0.08,
            "ret20_zz1000": 0.0,
            "ret20_kc50": 0.01,
            "ret20_dividend": 0.03,
            "excess5_kc_hs": -0.04,
            "excess20_kc_hs": -0.07,
            "excess5_zz_hs": -0.05,
            "excess20_zz_hs": -0.08,
            "excess5_div_hs": -0.02,
            "excess20_div_hs": -0.05,
        }
        state, _ = classify_style(feat)
        self.assertEqual(state, STATE_LARGE)

    def test_dividend_defense(self):
        feat = {
            "ok": True,
            "ret5_hs300": -0.02,
            "ret5_zz1000": -0.03,
            "ret5_kc50": -0.04,
            "ret5_dividend": 0.02,
            "ret20_hs300": -0.03,
            "ret20_zz1000": -0.05,
            "ret20_kc50": -0.06,
            "ret20_dividend": 0.04,
            "excess5_kc_hs": -0.02,
            "excess20_kc_hs": -0.03,
            "excess5_zz_hs": -0.01,
            "excess20_zz_hs": -0.02,
            "excess5_div_hs": 0.04,
            "excess20_div_hs": 0.07,
        }
        state, _ = classify_style(feat)
        self.assertEqual(state, STATE_DIVIDEND)

    def test_balanced(self):
        feat = {
            "ok": True,
            "ret5_hs300": 0.01,
            "ret5_zz1000": 0.012,
            "ret5_kc50": 0.011,
            "ret5_dividend": 0.009,
            "ret20_hs300": 0.03,
            "ret20_zz1000": 0.032,
            "ret20_kc50": 0.028,
            "ret20_dividend": 0.029,
            "excess5_kc_hs": 0.001,
            "excess20_kc_hs": -0.002,
            "excess5_zz_hs": 0.002,
            "excess20_zz_hs": 0.002,
            "excess5_div_hs": -0.001,
            "excess20_div_hs": -0.001,
        }
        state, _ = classify_style(feat)
        self.assertEqual(state, STATE_BALANCED)

    def test_balanced_requires_small_pairwise_spreads(self):
        feat = {
            "ok": True,
            "ret5_hs300": 0.0,
            "ret5_zz1000": -0.024,
            "ret5_kc50": 0.024,
            "ret5_dividend": 0.0,
            "ret20_hs300": 0.0,
            "ret20_zz1000": -0.024,
            "ret20_kc50": 0.024,
            "ret20_dividend": 0.0,
            "excess5_kc_hs": 0.024,
            "excess20_kc_hs": 0.024,
            "excess5_zz_hs": -0.024,
            "excess20_zz_hs": -0.024,
            "excess5_div_hs": 0.0,
            "excess20_div_hs": 0.0,
        }

        state, _ = classify_style(feat)

        self.assertNotEqual(state, STATE_BALANCED)

    def test_short_defense_mid_tech(self):
        """短线切红利、中线科创仍强 → 结论优先当下，并提示中线。"""
        feat = {
            "ok": True,
            "ret5_hs300": 0.01,
            "ret5_zz1000": -0.03,
            "ret5_kc50": -0.05,
            "ret5_dividend": 0.03,
            "ret20_hs300": -0.02,
            "ret20_zz1000": -0.08,
            "ret20_kc50": 0.09,
            "ret20_dividend": -0.01,
            "excess5_kc_hs": -0.06,
            "excess20_kc_hs": 0.11,
            "excess5_zz_hs": -0.04,
            "excess20_zz_hs": -0.06,
            "excess5_div_hs": 0.02,
            "excess20_div_hs": 0.01,
        }
        state, reasons = classify_style(feat)
        self.assertEqual(state, STATE_DIVIDEND)
        self.assertTrue(any("中线" in r for r in reasons))

    def test_states_defined(self):
        self.assertEqual(
            set(STATES),
            {STATE_TECH, STATE_SMALL, STATE_LARGE, STATE_DIVIDEND, STATE_BALANCED},
        )


if __name__ == "__main__":
    unittest.main()
