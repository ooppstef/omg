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

        _features, leaders = rot._fetch_stocks("881001", top_n=20)

        self.assertEqual(current.call_args.args[0], expected_codes)
        self.assertEqual([row["code"] for row in leaders], expected_codes)


if __name__ == "__main__":
    unittest.main()
