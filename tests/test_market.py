import unittest
from unittest.mock import patch

import pandas as pd

from stocks import market


class TestIndexCurrentPrice(unittest.TestCase):
    @patch("stocks.market.adata.stock.market.list_market_current")
    def test_stock_snapshot_excludes_invalid_prices(self, current):
        current.return_value = pd.DataFrame(
            [
                {
                    "stock_code": "600000",
                    "short_name": "有效",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "amount": 100.0,
                    "trade_date": "2026-07-21",
                },
                {
                    "stock_code": "600001",
                    "short_name": "无效",
                    "price": float("inf"),
                    "change_pct": 2.0,
                    "amount": 200.0,
                    "trade_date": "2026-07-21",
                },
            ]
        )

        rows = market.get_market_current_price(["600000", "600001"])

        self.assertEqual([row.code for row in rows], ["600000"])
        self.assertEqual(rows[0].as_of, "2026-07-21")

    @patch("stocks.market.adata.stock.market.ths_index.get_market_index")
    @patch("stocks.market.adata.stock.market.ths_index.get_market_index_current")
    def test_invalid_trade_date_does_not_invent_previous_close_day(
        self,
        current,
        history,
    ):
        current.return_value = pd.DataFrame(
            [
                {
                    "price": 106.0,
                    "change_pct": 1.25,
                    "amount": 1.0,
                    "trade_date": "not-a-date",
                }
            ]
        )
        history.return_value = pd.DataFrame(
            [{"trade_date": "2026-07-20", "close": 100.0}]
        )

        with patch.object(market, "_prev_close_cache", {}):
            result = market.get_index_current_price("999997", "测试指数")

        assert result is not None
        self.assertEqual(result.change_percent, 1.25)
        history.assert_not_called()

    @patch("stocks.market.adata.stock.market.ths_index.get_market_index")
    @patch("stocks.market.adata.stock.market.ths_index.get_market_index_current")
    def test_history_without_prior_row_marks_daily_change_unavailable(
        self,
        current,
        history,
    ):
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
        self.assertIsNone(result.change_percent)
        self.assertEqual(result.as_of, "2026-07-21")
        self.assertIn("--", str(result))

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
        self.assertIsNone(second.change_percent)


if __name__ == "__main__":
    unittest.main()
