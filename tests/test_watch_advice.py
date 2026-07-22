import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from stocks import watch_advice


def _fake_ohlcv(n: int = 80) -> pd.DataFrame:
    rows = []
    price = 10.0
    for i in range(n):
        price *= 1.01
        rows.append(
            {
                "code": "600000",
                "trade_date": f"2026-01-{(i % 28) + 1:02d}",
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1e6,
                "amount": 1e8,
                "change_pct": 1.0,
                "turnover": 1.0,
            }
        )
    # trade_date 上面会重复；改用顺序日
    for i, row in enumerate(rows):
        row["trade_date"] = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
    return pd.DataFrame(rows)


class TestWatchAdviceHelpers(unittest.TestCase):
    def test_tech_snapshot_has_core_fields(self):
        snap = watch_advice._tech_snapshot(_fake_ohlcv())
        self.assertIn("close", snap)
        self.assertIn("macd_hist", snap)
        self.assertIn("adx", snap)
        self.assertIn("mfi", snap)
        self.assertIn("vs_ma20_pct", snap)

    def test_fetch_tech_prefers_akshare(self):
        fake = _fake_ohlcv(80)
        with mock.patch(
            "stocks.watch_advice._fetch_kline_akshare", return_value=fake
        ) as mock_ak:
            with mock.patch(
                "stocks.watch_advice._fetch_kline_local",
                side_effect=AssertionError("local should not run"),
            ):
                snap, err = watch_advice._fetch_tech_for_code(
                    "300308", start="2025-01-01"
                )
        mock_ak.assert_called_once()
        self.assertIsNone(err)
        self.assertEqual(snap["source"], "akshare")
        self.assertIn("macd_hist", snap)

    def test_fetch_tech_falls_back_to_local(self):
        fake = _fake_ohlcv(80)
        with mock.patch(
            "stocks.watch_advice._fetch_kline_akshare",
            side_effect=RuntimeError("down"),
        ):
            with mock.patch(
                "stocks.watch_advice._fetch_kline_local", return_value=fake
            ):
                snap, err = watch_advice._fetch_tech_for_code(
                    "300308", start="2025-01-01"
                )
        self.assertIsNone(err)
        self.assertEqual(snap["source"], "local_db")
        self.assertTrue(snap.get("stale_warning"))

    def test_fetch_tech_silent_empty_akshare_uses_local(self):
        fake = _fake_ohlcv(80)
        with mock.patch(
            "stocks.watch_advice._fetch_kline_akshare",
            return_value=pd.DataFrame(),
        ):
            with mock.patch(
                "stocks.watch_advice._fetch_kline_local", return_value=fake
            ):
                snap, err = watch_advice._fetch_tech_for_code(
                    "300308", start="2025-01-01"
                )
        self.assertIsNone(err)
        self.assertEqual(snap["source"], "local_db")

    def test_fetch_tech_short_local_falls_through_to_adata(self):
        short = _fake_ohlcv(10)
        long = _fake_ohlcv(80)
        with mock.patch(
            "stocks.watch_advice._fetch_kline_akshare",
            return_value=pd.DataFrame(),
        ):
            with mock.patch(
                "stocks.watch_advice._fetch_kline_local", return_value=short
            ):
                with mock.patch(
                    "stocks.filter.db._fetch_one",
                    return_value=("300308", [
                        (
                            "300308",
                            row["trade_date"],
                            row["open"],
                            row["high"],
                            row["low"],
                            row["close"],
                            row["volume"],
                            row["amount"],
                            row["change_pct"],
                            row["turnover"],
                        )
                        for _, row in long.iterrows()
                    ], None),
                ):
                    snap, err = watch_advice._fetch_tech_for_code(
                        "300308", start="2025-01-01"
                    )
        self.assertIsNone(err)
        self.assertEqual(snap["source"], "adata")

    def test_sina_market_prefix(self):
        self.assertEqual(watch_advice._sina_market_prefix("600000"), "sh")
        self.assertEqual(watch_advice._sina_market_prefix("688347"), "sh")
        self.assertEqual(watch_advice._sina_market_prefix("300308"), "sz")
        self.assertEqual(watch_advice._sina_market_prefix("000892"), "sz")

    def test_board_match_rot_hot_crowd_etf(self):
        board = watch_advice._board_for_item(
            industry="半导体",
            concepts=["存储芯片", "先进封装"],
            rot_index={
                "存储芯片": {"group": "升温接力", "score": 12.0, "reason": "升温"}
            },
            hot_scores={"存储芯片": 8.5, "其他": 1.0},
            crowd_index={"半导体": {"crowd_pct": 88.0, "change_pct": 2.0}},
            etf_rows=[
                {
                    "theme": "半导体",
                    "code": "512480",
                    "name": "半导体ETF",
                    "short_state": "见顶",
                    "mid_state": "空头",
                    "change_pct": 3.0,
                }
            ],
        )
        self.assertEqual(board["rot"]["group"], "升温接力")
        self.assertEqual(board["hot"][0]["name"], "存储芯片")
        self.assertEqual(board["crowd"]["crowd_pct"], 88.0)
        self.assertEqual(board["etf"]["code"], "512480")

    def test_board_matches_industry_on_rot(self):
        board = watch_advice._board_for_item(
            industry="半导体",
            concepts=[],
            rot_index={"半导体": {"group": "回流优先", "score": 7.0, "reason": "回"}},
            hot_scores={"半导体": 5.0},
            crowd_index={},
            etf_rows=None,
        )
        self.assertEqual(board["rot"]["name"], "半导体")
        self.assertEqual(board["hot"][0]["name"], "半导体")


class TestWatchAdviceAnalyze(unittest.TestCase):
    def test_analyze_injected_skip_board(self):
        watch = [
            SimpleNamespace(
                code="600000", name="浦发银行", price=10.0, change_percent=1.2
            )
        ]
        tech = {
            "600000": {
                "as_of": "2026-07-21",
                "close": 10.0,
                "macd_hist": 0.1,
                "vs_ma20_pct": 2.0,
            }
        }
        out = watch_advice.analyze(
            skip_board=True,
            watch_rows=watch,
            index_rows=[],
            industry_by_code={"600000": "银行"},
            concepts_by_code={"600000": ["高股息"]},
            tech_by_code=tech,
            market={"mood": {"state": "分歧"}},
        )
        self.assertEqual(len(out["items"]), 1)
        item = out["items"][0]
        self.assertEqual(item["industry"], "银行")
        self.assertEqual(item["concepts"], ["高股息"])
        self.assertEqual(item["tech"]["close"], 10.0)
        self.assertIsNone(item["board"])
        self.assertTrue(out["meta"]["skip_board"])

    def test_analyze_with_board_alignment(self):
        watch = [
            SimpleNamespace(
                code="688981", name="中芯国际", price=50.0, change_percent=5.0
            )
        ]
        out = watch_advice.analyze(
            skip_board=False,
            watch_rows=watch,
            index_rows=[
                SimpleNamespace(
                    code="000001", name="上证指数", price=3200.0, change_percent=1.0
                )
            ],
            industry_by_code={"688981": "半导体"},
            concepts_by_code={"688981": ["芯片"]},
            tech_by_code={"688981": {"close": 50.0, "adx": 30.0}},
            market={"mood": {"state": "分歧"}, "style": {"state": "科技虹吸"}},
            rot_data={
                "升温接力": [
                    {"name": "芯片", "score": 9.0, "reason": "强"},
                ]
            },
            hot_data={"2026-07-21": [("芯片", 11.0)]},
            crowd_rows=[
                {"board_name": "半导体", "crowd_pct": 90.0, "change_pct": 4.0}
            ],
            etf_rows=[
                {
                    "theme": "芯片",
                    "code": "159995",
                    "name": "芯片ETF",
                    "short_state": "强趋势",
                    "mid_state": "多头",
                    "change_pct": 6.0,
                }
            ],
        )
        item = out["items"][0]
        self.assertEqual(item["board"]["rot"]["name"], "芯片")
        self.assertEqual(item["board"]["hot"][0]["score"], 11.0)
        self.assertEqual(item["board"]["crowd"]["crowd_pct"], 90.0)
        self.assertEqual(item["board"]["etf"]["code"], "159995")
        self.assertEqual(out["market"]["mood"]["state"], "分歧")
        self.assertEqual(out["indexes"][0]["name"], "上证指数")

    def test_truncate_max_n(self):
        watch = [
            SimpleNamespace(
                code=f"{i:06d}", name=f"S{i}", price=1.0, change_percent=0.0
            )
            for i in range(5)
        ]
        out = watch_advice.analyze(
            skip_board=True,
            max_n=2,
            watch_rows=watch,
            index_rows=[],
            industry_by_code={f"{i:06d}": None for i in range(5)},
            concepts_by_code={f"{i:06d}": [] for i in range(5)},
            tech_by_code={f"{i:06d}": {"close": 1.0} for i in range(5)},
        )
        self.assertEqual(len(out["items"]), 2)
        self.assertTrue(out["meta"]["truncated"])

    def test_empty_watchlist_skips_board_fetch(self):
        calls = {"crowd": 0, "etf": 0, "rot": 0}

        def boom_crowd(*_a, **_k):
            calls["crowd"] += 1
            raise AssertionError("crowd should not run")

        def boom_etf(*_a, **_k):
            calls["etf"] += 1
            raise AssertionError("etf should not run")

        def boom_rot(*_a, **_k):
            calls["rot"] += 1
            raise AssertionError("rot should not run")

        with mock.patch("stocks.crowd.scan_concept_crowding", side_effect=boom_crowd):
            with mock.patch("stocks.etf.scan_etf_states", side_effect=boom_etf):
                with mock.patch("stocks.rot.analyze", side_effect=boom_rot):
                    out = watch_advice.analyze(
                        skip_board=False,
                        watch_rows=[],
                        index_rows=[],
                        industry_by_code={},
                        concepts_by_code={},
                        tech_by_code={},
                    )
        self.assertEqual(out["items"], [])
        self.assertIn("watchlist:empty", out["errors"])
        self.assertFalse(out["meta"]["board_enabled"])
        self.assertEqual(calls, {"crowd": 0, "etf": 0, "rot": 0})
        from stocks.ai import tools as ai_tools

        with mock.patch("stocks.watch_advice.analyze", return_value=out):
            result = ai_tools.call("watch_advice", {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["items"], [])

    def test_board_fetch_uses_full_crowd_and_etf(self):
        watch = [
            SimpleNamespace(
                code="688981", name="中芯国际", price=50.0, change_percent=1.0
            )
        ]
        with mock.patch(
            "stocks.crowd.scan_concept_crowding",
            return_value=[
                {"board_name": "半导体", "crowd_pct": 40.0, "change_pct": 1.0}
            ],
        ) as mock_crowd:
            with mock.patch(
                "stocks.etf.scan_etf_states",
                return_value=[
                    {
                        "theme": "芯片",
                        "code": "159995",
                        "name": "芯片ETF",
                        "short_state": "强趋势",
                        "mid_state": "多头",
                        "change_pct": 2.0,
                    }
                ],
            ) as mock_etf:
                with mock.patch("stocks.rot.analyze", return_value={"升温接力": []}):
                    with mock.patch(
                        "stocks.mood.fetch_mood_history", return_value=[]
                    ):
                        with mock.patch(
                            "stocks.style.analyze",
                            return_value={"state": "均衡", "reasons": []},
                        ):
                            out = watch_advice.analyze(
                                skip_board=False,
                                watch_rows=watch,
                                index_rows=[],
                                industry_by_code={"688981": "半导体"},
                                concepts_by_code={"688981": ["芯片"]},
                                tech_by_code={"688981": {"close": 50.0}},
                                hot_data={"2026-07-21": [("芯片", 3.0)]},
                            )
        mock_crowd.assert_called_once()
        self.assertEqual(mock_crowd.call_args.kwargs.get("top"), 10_000)
        self.assertEqual(mock_crowd.call_args.kwargs.get("top_stocks"), 0)
        mock_etf.assert_called_once()
        self.assertFalse(mock_etf.call_args.kwargs.get("for_report"))
        self.assertEqual(out["items"][0]["board"]["crowd"]["crowd_pct"], 40.0)
        self.assertEqual(out["items"][0]["board"]["etf"]["code"], "159995")


class TestWatchAdviceTool(unittest.TestCase):
    def test_schema_and_handler_registered(self):
        from stocks.ai import tools as ai_tools

        names = {s["function"]["name"] for s in ai_tools.schemas()}
        self.assertIn("watch_advice", names)
        self.assertIn("watch_advice", ai_tools._HANDLERS)

    def test_tool_ok_with_inject(self):
        from stocks.ai import tools as ai_tools

        payload = {
            "as_of": "2026-07-21",
            "indexes": [],
            "market": {},
            "items": [{"code": "600000", "name": "甲", "errors": []}],
            "meta": {},
            "errors": [],
        }
        with mock.patch(
            "stocks.watch_advice.analyze", return_value=payload
        ):
            result = ai_tools.call("watch_advice", {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["items"][0]["code"], "600000")


if __name__ == "__main__":
    unittest.main()
