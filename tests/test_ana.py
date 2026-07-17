import unittest
from unittest.mock import patch

import pandas as pd

from stocks import ana


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


if __name__ == "__main__":
    unittest.main()
