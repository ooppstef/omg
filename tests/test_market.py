import unittest
from unittest.mock import patch

import pandas as pd

from stocks import market


class TestIndexCurrentPrice(unittest.TestCase):
    @patch("stocks.market.adata.stock.market.ths_index.get_market_index")
    @patch("stocks.market.adata.stock.market.ths_index.get_market_index_current")
    def test_history_without_prior_row_falls_back_to_open(self, current, history):
        current.return_value = pd.DataFrame(
            [
                {
                    "price": 106.0,
                    "open": 105.0,
                    "amount": 1.0,
                    "trade_date": "2026-07-21",
                }
            ]
        )
        history.return_value = pd.DataFrame(
            [{"trade_date": "2026-07-21", "close": 104.0}]
        )

        with patch.object(market, "_prev_close_cache", {}):
            result = market.get_index_current_price("999998", "测试指数")

        assert result is not None
        self.assertEqual(result.change_percent, 0.95)

    @patch("stocks.market.adata.stock.market.ths_index.get_market_index")
    @patch("stocks.market.adata.stock.market.ths_index.get_market_index_current")
    def test_history_failure_does_not_reuse_previous_trading_day_cache(
        self,
        current,
        history,
    ):
        current.side_effect = [
            pd.DataFrame(
                [
                    {
                        "price": 105.0,
                        "open": 101.0,
                        "amount": 1.0,
                        "trade_date": "2026-07-20",
                    }
                ]
            ),
            pd.DataFrame(
                [
                    {
                        "price": 106.0,
                        "open": 105.0,
                        "amount": 1.0,
                        "trade_date": "2026-07-21",
                    }
                ]
            ),
        ]
        history.side_effect = [
            pd.DataFrame([{"trade_date": "2026-07-19", "close": 100.0}]),
            RuntimeError("history unavailable"),
        ]

        with patch.object(market, "_prev_close_cache", {}):
            first = market.get_index_current_price("999999", "测试指数")
            second = market.get_index_current_price("999999", "测试指数")

        assert first is not None
        assert second is not None
        self.assertEqual(first.change_percent, 5.0)
        self.assertEqual(second.change_percent, 0.95)


if __name__ == "__main__":
    unittest.main()
