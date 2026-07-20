"""stocks.rank 单元测试（mock 现货，不打网络）。"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
