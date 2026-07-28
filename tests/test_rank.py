"""stocks.rank 单元测试（mock 现货，不打网络）。"""

import unittest
from unittest.mock import patch

import pandas as pd

from stocks import rank


def _spot_rows(rows):
    return pd.DataFrame(rows)


class TestRankFilter(unittest.TestCase):
    def test_excludes_st_bj_and_invalid_quotes(self):
        df = _spot_rows(
            [
                {
                    "代码": "600000",
                    "名称": "浦发银行",
                    "涨跌幅": 1.2,
                    "最新价": 10.0,
                    "成交额": 1e8,
                    "换手率": 1.0,
                },
                {
                    "代码": "600001",
                    "名称": "ST示例",
                    "涨跌幅": 9.0,
                    "最新价": 3.0,
                    "成交额": 1e8,
                    "换手率": 2.0,
                },
                {
                    "代码": "830001",
                    "名称": "北交示例",
                    "涨跌幅": 8.0,
                    "最新价": 5.0,
                    "成交额": 1e8,
                    "换手率": 2.0,
                },
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "涨跌幅": None,
                    "最新价": 12.0,
                    "成交额": 1e8,
                    "换手率": 1.0,
                },
                {
                    "代码": "000002",
                    "名称": "万科A",
                    "涨跌幅": -2.0,
                    "最新价": 0.0,
                    "成交额": 1e8,
                    "换手率": 1.0,
                },
            ]
        )
        out = rank.filter_universe(df)
        self.assertEqual(list(out["code"]), ["600000"])

    def test_includes_302_prefix_a_share(self):
        df = _spot_rows(
            [
                {
                    "代码": "302132",
                    "名称": "中航成飞",
                    "涨跌幅": 1.2,
                    "最新价": 55.76,
                    "成交额": 104_064_426,
                    "换手率": 0.32,
                }
            ]
        )

        out = rank.filter_universe(df)

        self.assertEqual(list(out["code"]), ["302132"])


class TestRankAnalyze(unittest.TestCase):
    def _frame(self):
        rows = []
        for i in range(10):
            rows.append(
                {
                    "代码": f"60010{i}",
                    "名称": f"涨股{i}",
                    "涨跌幅": 10 - i,
                    "最新价": 10.0 + i,
                    "成交额": 1e9 - i,
                    "换手率": 1.0 + i * 0.1,
                }
            )
        for i in range(10):
            rows.append(
                {
                    "代码": f"00020{i}",
                    "名称": f"跌股{i}",
                    "涨跌幅": -1 - i,
                    "最新价": 8.0 + i,
                    "成交额": 5e8,
                    "换手率": 2.0,
                }
            )
        # 同涨跌幅时按 code 稳定排序
        rows.append(
            {
                "代码": "300001",
                "名称": "并列涨",
                "涨跌幅": 10,
                "最新价": 20.0,
                "成交额": 1e8,
                "换手率": 3.0,
            }
        )
        return _spot_rows(rows)

    def test_sort_and_truncate(self):
        data = rank.analyze(
            gainers=3,
            losers=2,
            spot_df=self._frame(),
            industry_by_code={},
            concepts_by_code={},
        )
        self.assertEqual(data["meta"]["gainers_n"], 3)
        self.assertEqual(data["meta"]["losers_n"], 2)
        self.assertEqual(data["meta"]["scanned"], len(self._frame()))
        self.assertGreater(data["meta"]["filtered"], 0)
        self.assertIsNone(data["as_of"])
        self.assertEqual(data["as_of_dates"], [])
        self.assertIn("observed_at", data)
        self.assertEqual(
            data["data_time_status"],
            "provider_trade_date_unavailable",
        )
        # 涨幅 10 的两只：300001 先于 600100（code 升序）
        self.assertEqual(
            [r["code"] for r in data["gainers"]],
            ["300001", "600100", "600101"],
        )
        self.assertEqual(
            [r["code"] for r in data["losers"]],
            ["000209", "000208"],
        )

    def test_concepts_follow_hot_order_and_cap(self):
        spot = _spot_rows(
            [
                {
                    "代码": "600000",
                    "名称": "甲",
                    "涨跌幅": 5.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                },
                {
                    "代码": "600001",
                    "名称": "乙",
                    "涨跌幅": -5.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                },
            ]
        )
        data = rank.analyze(
            gainers=1,
            losers=1,
            spot_df=spot,
            industry_by_code={"600000": "银行", "600001": None},
            concepts_by_code={
                "600000": ["半导体", "人工智能", "机器人", "多余"],
                "600001": [],
            },
        )
        self.assertEqual(data["gainers"][0]["industry"], "银行")
        self.assertEqual(
            data["gainers"][0]["concepts"],
            ["半导体", "人工智能", "机器人"],
        )
        self.assertEqual(data["losers"][0]["industry"], None)
        self.assertEqual(data["losers"][0]["concepts"], [])
        self.assertEqual(data["meta"]["tag_miss"], 1)

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            rank.analyze(gainers=0, losers=1, spot_df=self._frame(), skip_tags=True)
        with self.assertRaises(ValueError):
            rank.analyze(gainers=1, losers=51, spot_df=self._frame(), skip_tags=True)

    def test_empty_universe_raises(self):
        df = _spot_rows(
            [
                {
                    "代码": "830001",
                    "名称": "北交",
                    "涨跌幅": 1.0,
                    "最新价": 1.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                }
            ]
        )
        with self.assertRaises(ValueError):
            rank.analyze(spot_df=df, skip_tags=True)

    def test_short_pool_truncates(self):
        df = _spot_rows(
            [
                {
                    "代码": "600000",
                    "名称": "甲",
                    "涨跌幅": 1.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                },
                {
                    "代码": "600001",
                    "名称": "乙",
                    "涨跌幅": -1.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                },
            ]
        )
        data = rank.analyze(
            gainers=50,
            losers=20,
            spot_df=df,
            industry_by_code={"600000": "银行", "600001": "地产"},
            concepts_by_code={"600000": ["银行"], "600001": ["地产"]},
        )
        self.assertEqual(len(data["gainers"]), 2)
        self.assertEqual(len(data["losers"]), 2)
        self.assertEqual(data["meta"]["gainers_n"], 50)
        self.assertEqual(data["meta"]["losers_n"], 20)
        self.assertEqual(data["gainers"][0]["code"], "600000")
        self.assertEqual(data["losers"][0]["code"], "600001")

    def test_fetch_spot_partial_falls_back_to_whole(self):
        sh = _spot_rows(
            [
                {
                    "代码": "600000",
                    "名称": "甲",
                    "涨跌幅": 1.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                }
            ]
        )
        whole = _spot_rows(
            [
                {
                    "代码": "600000",
                    "名称": "甲",
                    "涨跌幅": 1.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                },
                {
                    "代码": "000001",
                    "名称": "乙",
                    "涨跌幅": -1.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                },
            ]
        )

        def fake_board(fs, *, session=None):
            if fs == rank._FS_SH:
                return sh
            if fs == rank._FS_SZ:
                raise RuntimeError("sz down")
            if fs == rank._FS_HS:
                return whole
            raise AssertionError(fs)

        with patch("stocks.rank._fetch_em_spot_board", side_effect=fake_board):
            out = rank._fetch_spot()
        self.assertEqual(len(out), 2)

    def test_fetch_spot_rejects_half_market_without_fallback(self):
        sh = _spot_rows(
            [
                {
                    "代码": "600000",
                    "名称": "甲",
                    "涨跌幅": 1.0,
                    "最新价": 10.0,
                    "成交额": 1.0,
                    "换手率": 1.0,
                }
            ]
        )

        def fake_board(fs, *, session=None):
            if fs == rank._FS_SH:
                return sh
            raise RuntimeError("down")

        with patch("stocks.rank._fetch_em_spot_board", side_effect=fake_board):
            with self.assertRaises(ValueError):
                rank._fetch_spot()

    def test_normalize_em_diff_accepts_dict_and_list(self):
        self.assertEqual(rank._normalize_em_diff(None), [])
        self.assertEqual(rank._normalize_em_diff([]), [])
        self.assertEqual(
            rank._normalize_em_diff([{"f12": "600000"}]),
            [{"f12": "600000"}],
        )
        self.assertEqual(
            rank._normalize_em_diff({"0": {"f12": "600000"}, "1": {"f12": "000001"}}),
            [{"f12": "600000"}, {"f12": "000001"}],
        )

    def test_fetch_em_spot_board_handles_dict_diff(self):
        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "diff": {
                            "0": {
                                "f12": "600000",
                                "f14": "甲",
                                "f2": 10.0,
                                "f3": 1.5,
                                "f6": 1e8,
                                "f8": 2.0,
                            }
                        }
                    }
                }

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResp()

        out = rank._fetch_em_spot_board(rank._FS_SH, session=FakeSession())
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["代码"], "600000")
        self.assertEqual(out.iloc[0]["名称"], "甲")

    def test_fetch_em_spot_board_rejects_incomplete_pagination(self):
        class FakeResp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                return None

            def json(self):
                return {"data": self._data}

        class FakeSession:
            def __init__(self):
                self.page = 0

            def get(self, *args, **kwargs):
                self.page += 1
                if self.page == 1:
                    return FakeResp(
                        {
                            "total": 150,
                            "diff": [
                                {
                                    "f12": f"600{i:03d}",
                                    "f14": f"股票{i}",
                                    "f2": 10.0,
                                    "f3": 1.0,
                                    "f6": 1e8,
                                    "f8": 2.0,
                                }
                                for i in range(100)
                            ],
                        }
                    )
                return FakeResp({"total": 150, "diff": []})

        with self.assertRaisesRegex(ValueError, "分页不完整"):
            rank._fetch_em_spot_board(rank._FS_SH, session=FakeSession())

    def test_live_concepts_follow_hot_name_order(self):
        hot = ["机器人", "半导体", "人工智能"]
        needed = {"600000", "600001"}

        def fake_constituent(index_code):
            mapping = {
                "C1": pd.DataFrame({"stock_code": ["600000"]}),
                "C2": pd.DataFrame({"stock_code": ["600000", "600001"]}),
                "C3": pd.DataFrame({"stock_code": ["600000"]}),
            }
            return mapping[index_code]

        concept_df = pd.DataFrame(
            [
                {"name": "机器人", "index_code": "C1"},
                {"name": "半导体", "index_code": "C2"},
                {"name": "人工智能", "index_code": "C3"},
            ]
        )
        cons = rank._live_concepts_by_code(
            hot,
            needed,
            concept_df=concept_df,
            fetch_constituent=fake_constituent,
        )
        self.assertEqual(cons["600000"], ["机器人", "半导体", "人工智能"])
        self.assertEqual(cons["600001"], ["半导体"])


if __name__ == "__main__":
    unittest.main()
