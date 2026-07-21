import datetime
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from stocks.etf import (
    CHANGE_SOURCE_EM_SPOT,
    STATE_DOWN,
    STATES,
    _scale_em_price,
    build_comment,
    build_reason_block,
    classify_etf,
    classify_horizon,
    compute_features,
    ensure_etf_as_of,
    fetch_etf_spot_em,
    load_universe,
    resolve_target_as_of,
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
            "as_of": "2026-07-21",
            "target_as_of": "2026-07-21",
            "change_source": "adata",
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
        self.assertIn("当日涨跌", text)
        block = build_reason_block(row)
        self.assertIn("短线理由", block)
        self.assertIn("中线理由", block)
        self.assertIn("当日涨跌", block)

    def test_reason_block_marks_stale_as_of(self):
        row = {
            "change_pct": 4.6,
            "as_of": "2026-07-20",
            "target_as_of": "2026-07-21",
            "change_source": "adata",
            "short_reasons": ["短线强趋势"],
            "mid_reasons": [],
        }
        block = build_reason_block(row)
        self.assertIn("截至2026-07-20", block)
        self.assertIn("非目标日2026-07-21", block)
        self.assertNotIn("当日涨跌:", block)

    def test_ensure_etf_as_of_patches_from_spot(self):
        frame = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-20",
                    "close": 0.432,
                    "amount": 1e9,
                    "change_pct": 4.6,
                }
            ]
        )
        spot = {
            "code": "512690",
            "name": "酒ETF鹏华",
            "close": 0.425,
            "change_pct": -1.62,
            "amount": 1.1e9,
            "trade_date": "2026-07-21",
        }
        with patch("stocks.etf.fetch_etf_spot_em", return_value=spot):
            out, meta = ensure_etf_as_of(frame, "512690", "2026-07-21")
        self.assertTrue(meta["patched"])
        self.assertEqual(meta["as_of"], "2026-07-21")
        self.assertEqual(meta["change_source"], CHANGE_SOURCE_EM_SPOT)
        self.assertEqual(str(out.iloc[-1]["trade_date"]), "2026-07-21")
        self.assertAlmostEqual(float(out.iloc[-1]["change_pct"]), -1.62)

    def test_ensure_etf_as_of_trims_rows_after_target(self):
        frame = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-20",
                    "close": 0.432,
                    "amount": 1e9,
                    "change_pct": 1.2,
                },
                {
                    "trade_date": "2026-07-21",
                    "close": 0.425,
                    "amount": 1.1e9,
                    "change_pct": -1.62,
                },
            ]
        )

        with patch("stocks.etf.fetch_etf_spot_em", return_value=None):
            out, meta = ensure_etf_as_of(frame, "512690", "2026-07-20")

        self.assertEqual(meta["as_of"], "2026-07-20")
        self.assertFalse(meta["patched"])
        self.assertEqual(out["trade_date"].tolist(), ["2026-07-20"])
        self.assertAlmostEqual(float(out.iloc[-1]["change_pct"]), 1.2)

    def test_ensure_keeps_patched_close_when_amount_missing(self):
        """amount 缺失时不得让特征对齐丢掉补齐日收盘价。"""
        n = 80
        idx = pd.bdate_range("2026-03-01", periods=n)
        hist = pd.DataFrame(
            {
                "trade_date": idx.strftime("%Y-%m-%d"),
                "close": np.linspace(0.4, 0.43, n),
                "amount": [1e9] * n,
                "change_pct": [0.1] * n,
            }
        )
        spot = {
            "code": "512690",
            "name": "酒ETF",
            "close": 0.425,
            "change_pct": -1.62,
            "amount": None,
            "trade_date": "2026-07-21",
        }
        with patch("stocks.etf.fetch_etf_spot_em", return_value=spot):
            out, meta = ensure_etf_as_of(hist, "512690", "2026-07-21")
        self.assertTrue(meta["patched"])
        self.assertGreater(float(out.iloc[-1]["amount"]), 0)
        close = out.set_index("trade_date")["close"]
        amount = out.set_index("trade_date")["amount"]
        bench = close * 0.99
        feat = compute_features(close, amount, bench)
        self.assertTrue(feat["ok"])
        self.assertAlmostEqual(float(feat["close"]), 0.425, places=4)

    def test_ensure_refuses_bad_spot(self):
        frame = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-20",
                    "close": 0.432,
                    "amount": 1e9,
                    "change_pct": 4.6,
                }
            ]
        )
        with patch("stocks.etf.fetch_etf_spot_em", return_value=None):
            out, meta = ensure_etf_as_of(frame, "512690", "2026-07-21")
        self.assertFalse(meta["patched"])
        self.assertEqual(meta["as_of"], "2026-07-20")
        self.assertEqual(len(out), 1)

    def test_ensure_refuses_snapshot_from_different_trade_date(self):
        frame = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-17",
                    "close": 0.432,
                    "amount": 1e9,
                    "change_pct": 1.2,
                }
            ]
        )
        spot = {
            "close": 0.425,
            "change_pct": -1.62,
            "amount": 1.1e9,
            "trade_date": "2026-07-21",
        }

        with patch("stocks.etf.fetch_etf_spot_em", return_value=spot):
            out, meta = ensure_etf_as_of(frame, "512690", "2026-07-20")

        self.assertFalse(meta["patched"])
        self.assertEqual(meta["as_of"], "2026-07-17")
        self.assertEqual(out["trade_date"].tolist(), ["2026-07-17"])

    def test_scale_em_price_rejects_outlier(self):
        self.assertIsNone(_scale_em_price(999999, prev_close=0.432))
        self.assertAlmostEqual(_scale_em_price(425, prev_close=0.432), 0.425, places=4)

    def test_fetch_etf_spot_includes_snapshot_trade_date(self):
        response = Mock()
        response.json.return_value = {
            "data": {
                "f43": 425,
                "f47": 100,
                "f48": 1.1e9,
                "f57": "512690",
                "f58": "酒ETF",
                "f60": 432,
                "f86": 1784621505,
                "f169": -7,
                "f170": -162,
            }
        }
        session = Mock()
        session.get.return_value = response

        with patch("stocks.etf._em_session", return_value=session):
            spot = fetch_etf_spot_em("512690", prev_close=0.432)

        assert spot is not None
        self.assertEqual(spot["trade_date"], "2026-07-21")

    def test_change_label_for_patched_spot(self):
        row = {
            "change_pct": -1.62,
            "as_of": "2026-07-21",
            "target_as_of": "2026-07-21",
            "change_source": CHANGE_SOURCE_EM_SPOT,
            "short_reasons": ["短线强趋势"],
            "mid_reasons": [],
        }
        block = build_reason_block(row)
        self.assertIn("东财实时补齐", block)

    def test_resolve_target_uses_bench_last_when_current(self):
        frame = pd.DataFrame(
            [{"trade_date": "2026-07-21", "close": 4.787, "amount": 1.0, "change_pct": 2.9}]
        )
        with patch("stocks.etf.datetime") as mock_dt, patch(
            "stocks.etf.fetch_etf_spot_em"
        ) as mock_spot:
            mock_dt.date.today.return_value = datetime.date(2026, 7, 21)
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 7, 21, 16, 0, 0)
            self.assertEqual(resolve_target_as_of(frame), "2026-07-21")
            mock_spot.assert_not_called()

    def test_resolve_target_rejects_stale_snapshot_with_positive_amount(self):
        frame = pd.DataFrame(
            [{"trade_date": "2026-07-20", "close": 4.65, "amount": 1.0}]
        )
        stale_spot = {
            "close": 4.65,
            "amount": 1.2e10,
            "trade_date": "2026-07-20",
        }
        with patch("stocks.etf.datetime") as mock_dt, patch(
            "stocks.etf.fetch_etf_spot_em", return_value=stale_spot
        ):
            mock_dt.date.today.return_value = datetime.date(2026, 7, 21)
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 7, 21, 10, 0, 0)
            self.assertEqual(resolve_target_as_of(frame), "2026-07-20")

    def test_resolve_target_uses_current_snapshot_with_positive_amount(self):
        frame = pd.DataFrame(
            [{"trade_date": "2026-07-20", "close": 4.65, "amount": 1.0}]
        )
        current_spot = {
            "close": 4.787,
            "amount": 1.2e10,
            "trade_date": "2026-07-21",
        }
        with patch("stocks.etf.datetime") as mock_dt, patch(
            "stocks.etf.fetch_etf_spot_em", return_value=current_spot
        ):
            mock_dt.date.today.return_value = datetime.date(2026, 7, 21)
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 7, 21, 10, 0, 0)
            self.assertEqual(resolve_target_as_of(frame), "2026-07-21")

    def test_normalize_missing_close_returns_empty(self):
        from stocks.etf import _normalize_etf_frame

        df = pd.DataFrame({"trade_date": ["2026-07-21"], "open": [1.0]})
        out = _normalize_etf_frame(df, "2026-01-01")
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
