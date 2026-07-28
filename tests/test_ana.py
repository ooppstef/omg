import unittest
from unittest.mock import patch

import pandas as pd

from stocks import ana
from stocks.industry_models import IndustryConcept


class TestConceptStrengthChart(unittest.TestCase):
    @patch("stocks.ana._get_benchmark_returns", return_value={"2099-01-02": 0.0})
    @patch("stocks.ana.adata.stock.info.all_concept_code_ths", return_value=None)
    def test_chart_handles_missing_concept_list(self, _concept_list, _benchmark):
        self.assertIsNone(ana.concept_strength_chart(["半导体"], days=10))

    @patch("matplotlib.pyplot.show")
    @patch("stocks.ana._get_benchmark_returns", return_value={"2099-01-02": 0.0})
    @patch("stocks.ana.adata.stock.market.get_market_concept_ths")
    @patch("stocks.ana.adata.stock.info.all_concept_code_ths")
    def test_chart_matches_user_text_literally(
        self,
        concept_list,
        concept_market,
        _benchmark,
        _show,
    ):
        concept_list.return_value = pd.DataFrame(
            [{"index_code": "A", "name": "半[导体"}]
        )
        concept_market.return_value = pd.DataFrame(
            {
                "trade_date": ["2099-01-02"],
                "change_pct": [1.0],
                "amount": [100.0],
            }
        )

        result = ana.concept_strength_chart(["["], days=10)

        self.assertIn("半[导体", result)

    @patch("matplotlib.pyplot.show")
    @patch("stocks.ana._get_benchmark_returns")
    @patch("stocks.ana.adata.stock.market.get_market_concept_ths")
    @patch("stocks.ana.adata.stock.info.all_concept_code_ths")
    def test_each_concept_uses_its_own_aligned_dates(
        self,
        concept_list,
        concept_market,
        benchmark,
        _show,
    ):
        concept_list.return_value = pd.DataFrame(
            [
                {"index_code": "A", "name": "概念甲"},
                {"index_code": "B", "name": "概念乙"},
            ]
        )
        concept_market.side_effect = [
            pd.DataFrame(
                {
                    "trade_date": ["2099-01-02", "2099-01-03"],
                    "change_pct": [1.0, 2.0],
                    "amount": [100.0, 100.0],
                }
            ),
            pd.DataFrame(
                {
                    "trade_date": ["2099-01-03"],
                    "change_pct": [3.0],
                    "amount": [100.0],
                }
            ),
        ]
        benchmark.return_value = {
            "2099-01-02": 0.0,
            "2099-01-03": 0.0,
        }

        result = ana.concept_strength_chart(["概念甲", "概念乙"], days=10)

        self.assertEqual(len(result["概念甲"]), 2)
        self.assertEqual(len(result["概念乙"]), 1)


class TestConceptStrengthRanking(unittest.TestCase):
    @patch("stocks.ana._get_benchmark_returns", return_value={"2099-01-02": 0.0})
    @patch("stocks.ana.adata.stock.market.get_market_concept_ths")
    @patch("stocks.ana.adata.stock.info.all_concept_code_ths")
    def test_nonpositive_turnover_is_excluded(
        self,
        concept_list,
        concept_market,
        _benchmark,
    ):
        concept_list.return_value = pd.DataFrame(
            [
                {"index_code": "A", "name": "无成交概念"},
                {"index_code": "B", "name": "有效概念"},
            ]
        )

        def market(index_code):
            amount = 0.0 if index_code == "A" else 100.0
            return pd.DataFrame(
                {
                    "trade_date": ["2099-01-02"],
                    "change_pct": [2.0],
                    "amount": [amount],
                }
            )

        concept_market.side_effect = market

        result = ana.concept_strength_top10(days=1, top=10, print_result=False)

        self.assertEqual(
            [name for name, _score in result["2099-01-02"]],
            ["有效概念"],
        )

    @patch("stocks.ana._get_benchmark_returns", return_value={"2099-01-02": 0.0})
    @patch("stocks.ana.adata.stock.market.get_market_concept_ths")
    @patch("stocks.ana.adata.stock.info.all_concept_code_ths")
    def test_non_finite_market_values_are_excluded(
        self,
        concept_list,
        concept_market,
        _benchmark,
    ):
        concept_list.return_value = pd.DataFrame(
            [
                {"index_code": "A", "name": "无效概念"},
                {"index_code": "B", "name": "有效概念"},
            ]
        )

        def market(index_code):
            change = float("nan") if index_code == "A" else 2.0
            return pd.DataFrame(
                {
                    "trade_date": ["2099-01-02"],
                    "change_pct": [change],
                    "amount": [100.0],
                }
            )

        concept_market.side_effect = market

        result = ana.concept_strength_top10(days=1, top=10, print_result=False)

        self.assertEqual(
            [name for name, _score in result["2099-01-02"]],
            ["有效概念"],
        )


class TestHotListDataQuality(unittest.TestCase):
    @patch("stocks.ana.adata.sentiment.hot.pop_rank_100_east")
    def test_missing_amount_is_not_fabricated_as_zero(self, pop_rank):
        pop_rank.return_value = pd.DataFrame(
            [
                {
                    "stock_code": "600000",
                    "short_name": "测试",
                    "price": 10.0,
                    "change_pct": 1.0,
                }
            ]
        )

        rows = ana.east_top_list(print_result=False)

        self.assertIsNone(rows[0].amount)

    def test_industry_concept_keeps_missing_metrics_missing(self):
        concept = IndustryConcept.from_dict(
            {"concept_code": "A", "concept_name": "测试"}
        )

        self.assertIsNone(concept.change_pct)
        self.assertIsNone(concept.hot_value)
        self.assertIn("涨跌幅:", str(concept))


if __name__ == "__main__":
    unittest.main()
