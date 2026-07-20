import unittest
from unittest.mock import patch

import math

from stocks.mood import (
    STATES,
    STATE_ICE,
    STATE_REPAIR,
    STATE_MAIN,
    STATE_DIVERGE,
    STATE_EBB,
    STATE_NEUTRAL,
    classify_mood,
    compute_mood_metrics,
    fetch_mood_history,
    zhaban_rate,
)


class TestMoodMetrics(unittest.TestCase):
    def test_zhaban_rate(self):
        self.assertAlmostEqual(zhaban_rate(10, 10), 0.5)
        self.assertAlmostEqual(zhaban_rate(0, 0), 0.0)
        self.assertAlmostEqual(zhaban_rate(20, 5), 5 / 25)

    def test_compute_from_pools(self):
        zt = [
            {"代码": "1", "连板数": 1},
            {"代码": "2", "连板数": 5},
            {"代码": "3", "连板数": 2},
        ]
        zb = [{"代码": "a"}, {"代码": "b"}]
        dt = [{"代码": "x"}]
        prev = [
            {"代码": "1", "涨跌幅": 3.0},
            {"代码": "2", "涨跌幅": -1.0},
            {"代码": "3", "涨跌幅": 5.0},
        ]
        m = compute_mood_metrics(zt, zb, dt, prev)
        self.assertEqual(m["limit_up"], 3)
        self.assertEqual(m["limit_down"], 1)
        self.assertEqual(m["zhaban"], 2)
        self.assertEqual(m["height"], 5)
        self.assertAlmostEqual(m["premium"], (3.0 - 1.0 + 5.0) / 3.0)
        self.assertAlmostEqual(m["first_board_ratio"], 1 / 3)

    def test_compute_ignores_non_finite_premiums(self):
        m = compute_mood_metrics(
            [{"代码": "1", "连板数": 5}],
            [],
            [],
            [
                {"代码": "1", "涨跌幅": -8.0},
                {"代码": "2", "涨跌幅": float("nan")},
                {"代码": "3", "涨跌幅": float("inf")},
            ],
        )
        self.assertFalse(math.isnan(m["premium"]))
        self.assertEqual(m["premium"], -8.0)

    @patch("stocks.mood._recent_calendar_dates", return_value=["20260716"])
    @patch("stocks.mood.fetch_pools_for_date")
    def test_history_skips_date_when_critical_pool_fails(
        self, fetch_pools, _recent_dates
    ):
        fetch_pools.return_value = {
            "date": "20260716",
            "zt": [{"代码": str(i), "连板数": 5} for i in range(80)],
            "zb": [],
            "dt": [],
            "prev": [],
            "errors": ["dt:TimeoutError"],
        }

        self.assertEqual(fetch_mood_history(days=1), [])


class TestClassifyMood(unittest.TestCase):
    def test_main_up(self):
        m = {
            "limit_up": 80,
            "limit_down": 3,
            "zhaban": 10,
            "zhaban_rate": 10 / 90,
            "height": 6,
            "premium": 4.0,
            "first_board_ratio": 0.4,
            "ok": True,
        }
        state, reasons = classify_mood(m)
        self.assertEqual(state, STATE_MAIN)
        self.assertTrue(any("涨停" in r for r in reasons))

    def test_ebb_by_limit_down(self):
        m = {
            "limit_up": 25,
            "limit_down": 35,
            "zhaban": 5,
            "zhaban_rate": 0.2,
            "height": 2,
            "premium": -2.0,
            "first_board_ratio": 0.7,
            "ok": True,
        }
        state, _ = classify_mood(m)
        self.assertEqual(state, STATE_EBB)

    def test_ebb_by_high_zhaban(self):
        m = {
            "limit_up": 20,
            "limit_down": 8,
            "zhaban": 30,
            "zhaban_rate": 30 / 50,
            "height": 2,
            "premium": -1.0,
            "first_board_ratio": 0.8,
            "ok": True,
        }
        state, _ = classify_mood(m)
        self.assertEqual(state, STATE_EBB)

    def test_diverge(self):
        m = {
            "limit_up": 45,
            "limit_down": 15,
            "zhaban": 25,
            "zhaban_rate": 25 / 70,
            "height": 4,
            "premium": 0.5,
            "first_board_ratio": 0.5,
            "ok": True,
        }
        state, _ = classify_mood(m)
        self.assertEqual(state, STATE_DIVERGE)

    def test_diverge_high_up_weak_height(self):
        m = {
            "limit_up": 81,
            "limit_down": 22,
            "zhaban": 21,
            "zhaban_rate": 21 / 102,
            "height": 3,
            "premium": 3.6,
            "first_board_ratio": 0.7,
            "ok": True,
        }
        state, reasons = classify_mood(m)
        self.assertEqual(state, STATE_DIVERGE)
        self.assertTrue(any("高度不足" in r for r in reasons))

    def test_repair(self):
        m = {
            "limit_up": 40,
            "limit_down": 5,
            "zhaban": 8,
            "zhaban_rate": 8 / 48,
            "height": 3,
            "premium": 2.0,
            "first_board_ratio": 0.6,
            "ok": True,
        }
        state, _ = classify_mood(m)
        self.assertEqual(state, STATE_REPAIR)

    def test_sixty_limit_ups_with_low_height_is_repair(self):
        m = {
            "limit_up": 60,
            "limit_down": 5,
            "zhaban": 8,
            "zhaban_rate": 8 / 68,
            "height": 3,
            "premium": 2.0,
            "first_board_ratio": 0.6,
            "ok": True,
        }

        state, _ = classify_mood(m)

        self.assertEqual(state, STATE_REPAIR)

    def test_ice(self):
        m = {
            "limit_up": 15,
            "limit_down": 12,
            "zhaban": 5,
            "zhaban_rate": 5 / 20,
            "height": 2,
            "premium": -3.0,
            "first_board_ratio": 0.9,
            "ok": True,
        }
        state, _ = classify_mood(m)
        self.assertEqual(state, STATE_ICE)

    def test_ice_not_rate_diverge(self):
        """冷市即使炸板率在 30–50%，也应冰点而非分歧。"""
        m = {
            "limit_up": 18,
            "limit_down": 8,
            "zhaban": 12,
            "zhaban_rate": 12 / 30,
            "height": 2,
            "premium": -1.0,
            "first_board_ratio": 0.8,
            "ok": True,
        }
        state, _ = classify_mood(m)
        self.assertEqual(state, STATE_ICE)

    def test_metrics_ok_requires_intraday_pools(self):
        m = compute_mood_metrics([], [], [], [{"代码": "1", "涨跌幅": 1.0}])
        self.assertFalse(m["ok"])


    def test_insufficient_data(self):
        state, reasons = classify_mood({"ok": False})
        self.assertEqual(state, STATE_NEUTRAL)
        self.assertTrue(any("不足" in r for r in reasons))

    def test_states_cover(self):
        self.assertEqual(
            set(STATES),
            {
                STATE_ICE,
                STATE_REPAIR,
                STATE_MAIN,
                STATE_DIVERGE,
                STATE_EBB,
                STATE_NEUTRAL,
            },
        )


if __name__ == "__main__":
    unittest.main()
