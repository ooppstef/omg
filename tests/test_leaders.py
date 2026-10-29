import unittest

import pandas as pd

from stocks.leaders import (
    _limit_pct,
    _supported_code,
    build_item,
    summarize,
)


def _frame(open_, high, low, close, prev_close=100.0, date="2026-10-28"):
    return pd.DataFrame(
        [
            {
                "trade_date": "2026-10-27",
                "open": prev_close,
                "high": prev_close,
                "low": prev_close,
                "close": prev_close,
            },
            {
                "trade_date": date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
            },
        ]
    )


def _item(code, ret10, max_gain, close_gain, opened=False, fade=None):
    return {
        "code": code,
        "name": code,
        "ret10": ret10,
        "max_gain": max_gain,
        "close_gain": close_gain,
        "fade_pp": max_gain - close_gain if fade is None else fade,
        "opened_limit": opened,
        "leader_break": ret10 >= 30 and close_gain < 0 and (max_gain - close_gain) >= 4,
        "fade": max_gain >= 3 and (max_gain - close_gain) >= 4,
    }


class TestLeaderHelpers(unittest.TestCase):
    def test_limit_pct(self):
        self.assertEqual(_limit_pct("301313"), 20.0)
        self.assertEqual(_limit_pct("688585"), 20.0)
        self.assertEqual(_limit_pct("603109"), 10.0)

    def test_supported_code(self):
        self.assertTrue(_supported_code("301313"))
        self.assertTrue(_supported_code("603109"))
        self.assertFalse(_supported_code("920207"))

    def test_build_item_flags_top_leader_failure(self):
        # 前收 100，创业板最高 120（+20% 触板），收盘 98（-2%）→ 回落 22pp
        item = build_item("301313", "凡拓数创", 84.3, _frame(101, 120, 97, 98))
        self.assertTrue(item["hit_limit"])
        self.assertTrue(item["opened_limit"])
        self.assertTrue(item["leader_break"])
        self.assertTrue(item["fade"])
        self.assertAlmostEqual(item["max_gain"], 20.0)
        self.assertAlmostEqual(item["close_gain"], -2.0)
        self.assertAlmostEqual(item["fade_pp"], 22.0)

    def test_build_item_ignores_stale_bar(self):
        item = build_item(
            "301313", "凡拓数创", 84.3, _frame(101, 110, 97, 98, date="2026-10-27"),
            expected_date="2026-10-28",
        )
        self.assertEqual(item, {})


class TestLeaderSummary(unittest.TestCase):
    def test_s_point_confirmed_by_leader_and_breadth(self):
        items = [
            _item("301313", 84.3, 6.8, -2.2),
            _item("688071", 76.8, 13.7, 4.5),
            _item("301502", 67.1, 2.9, -1.7),
            _item("688836", 65.7, 15.5, 8.7),
            _item("301428", 62.6, 20.0, 14.0, opened=True),
            _item("688585", 62.6, 20.0, 13.3, opened=True),
            _item("688286", 57.8, 19.5, 8.7, opened=True),
            _item("300307", 56.8, 7.8, 2.3),
            _item("603028", 55.9, 6.5, 0.7),
            _item("003021", 54.6, 10.0, 5.3),
        ]
        summary = summarize(items, theme="机器人", session="收盘")
        self.assertEqual(summary["stage"], "S点确认（分时）")
        self.assertTrue(summary["top_leader_fail"])
        self.assertTrue(summary["broad_fade"])
        self.assertEqual(summary["n_fade"], 9)
        self.assertEqual(summary["n_limit_open"], 3)
        self.assertGreater(summary["score"], 0)

    def test_warning_when_only_breadth_fades(self):
        items = [
            _item("301313", 84.3, 7.0, 1.0),  # 龙头未失败
            _item("688071", 76.8, 13.7, 4.5),
            _item("301502", 67.1, 6.9, -1.0),
            _item("688836", 65.7, 15.5, 8.7),
            _item("301428", 62.6, 20.0, 14.0),
            _item("688585", 62.6, 20.0, 13.3),
            _item("688286", 57.8, 19.5, 8.7),
            _item("300307", 56.8, 7.8, 2.3),
            _item("603028", 55.9, 6.5, 0.7),
            _item("003021", 54.6, 10.0, 5.3),
        ]
        summary = summarize(items, theme="机器人", session="收盘")
        self.assertFalse(summary["top_leader_fail"])
        self.assertTrue(summary["broad_fade"])
        self.assertEqual(summary["stage"], "S点预警")

    def test_no_data(self):
        summary = summarize([], theme="机器人", session="收盘")
        self.assertEqual(summary["stage"], "数据缺口")
        self.assertIsNone(summary["score"])
