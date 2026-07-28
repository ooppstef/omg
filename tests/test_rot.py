import unittest
from unittest.mock import patch

import pandas as pd

from stocks import rot


class TestRotationConstituents(unittest.TestCase):
    @patch("stocks.rot.adata.stock.market.list_market_current")
    @patch("stocks.rot.adata.stock.info.concept_constituent_ths")
    def test_fetch_stocks_keeps_all_supported_a_share_prefixes(
        self,
        constituents,
        current,
    ):
        expected_codes = [
            "600001",
            "601001",
            "603001",
            "605001",
            "000001",
            "001001",
            "002001",
            "003001",
            "300001",
            "301001",
            "688001",
        ]
        constituents.return_value = pd.DataFrame(
            {"stock_code": [*expected_codes, "430001"]}
        )

        def quote_rows(codes):
            return pd.DataFrame(
                [
                    {
                        "stock_code": code,
                        "short_name": f"股票{code}",
                        "change_pct": 1.0,
                        "price": 10.0,
                    }
                    for code in codes
                ]
            )

        current.side_effect = quote_rows

        features, leaders = rot._fetch_stocks("881001", top_n=20)

        self.assertEqual(current.call_args.args[0], expected_codes)
        self.assertEqual([row["code"] for row in leaders], expected_codes)
        self.assertFalse(features["breadth_available"])
        self.assertEqual(
            features["date_status"],
            "provider_trade_date_unavailable",
        )


class TestRotationMath(unittest.TestCase):
    def test_stock_confirm_uses_all_valid_constituents_for_breadth(self):
        quotes = pd.DataFrame(
            {
                "change_pct": [10.0] * 5 + [-1.0] * 95,
            }
        )

        features = rot.stock_confirm(quotes)

        self.assertEqual(features["top5_avg_change_pct"], 10.0)
        self.assertEqual(features["positive_ratio"], 0.05)

    def test_stock_score_does_not_reward_leader_concentration(self):
        concentrated = {
            "top5_avg_change_pct": 5.0,
            "positive_ratio": 0.80,
            "leader_concentration": 0.95,
        }
        distributed = {
            "top5_avg_change_pct": 5.0,
            "positive_ratio": 0.80,
            "leader_concentration": 0.40,
        }

        self.assertGreaterEqual(
            rot._stock_score(distributed),
            rot._stock_score(concentrated),
        )

    def test_incomplete_constituent_snapshot_cannot_confirm_breadth(self):
        partial = pd.DataFrame(
            {
                "code": [f"{index:06d}" for index in range(5)],
                "change_pct": [10.0] * 5,
            }
        )

        features = rot.stock_confirm(partial, expected_count=100)

        self.assertFalse(features["breadth_available"])
        self.assertEqual(features["coverage_ratio"], 0.05)
        self.assertEqual(rot._stock_score(features), 0.0)

    def test_breadth_uses_one_latest_quote_date(self):
        quotes = pd.DataFrame(
            {
                "code": ["600001", "600002", "600003"],
                "change_pct": [1.0, -1.0, 10.0],
                "as_of": ["2026-07-21", "2026-07-21", "2026-07-18"],
            }
        )

        features = rot.stock_confirm(quotes, expected_count=3)

        self.assertEqual(features["as_of"], "2026-07-21")
        self.assertEqual(features["constituent_count"], 2)
        self.assertAlmostEqual(features["positive_ratio"], 0.5)
        self.assertFalse(features["breadth_available"])

    def test_daily_strength_excludes_missing_or_nonpositive_turnover(self):
        concepts = pd.DataFrame(
            {
                "trade_date": [
                    "2026-07-17",
                    "2026-07-18",
                    "2026-07-19",
                    "2026-07-20",
                ],
                "change_pct": [2.0, None, 1.0, 1.0],
                "amount": [100.0, 100.0, None, 0.0],
            }
        )
        benchmark = {
            "2026-07-17": 1.0,
            "2026-07-18": 1.0,
            "2026-07-19": 1.0,
            "2026-07-20": 1.0,
        }

        result = rot.daily_strength(concepts, benchmark)

        self.assertEqual(result["trade_date"].tolist(), ["2026-07-17"])

    def test_daily_strength_excludes_invalid_trade_dates(self):
        concepts = pd.DataFrame(
            {
                "trade_date": ["not-a-date", "2026-07-17"],
                "change_pct": [9.0, 2.0],
                "amount": [100.0, 100.0],
            }
        )
        benchmark = {"not-a-date": 0.0, "2026-07-17": 1.0}

        result = rot.daily_strength(concepts, benchmark)

        self.assertEqual(result["trade_date"].tolist(), ["2026-07-17"])


if __name__ == "__main__":
    unittest.main()
