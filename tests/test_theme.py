"""stocks.theme 单元测试（mock 接口，不打网络）。"""

import unittest
from unittest.mock import patch

from stocks import theme


def _table_payload(fields: str, data: list, total_pages: int = 1) -> dict:
    return {
        "re": True,
        "message": "",
        "result": [
            {
                "TableName": "T",
                "TotalPage": total_pages,
                "SplitSymbol": "|",
                "FieldName": fields,
                "Data": data,
            }
        ],
    }


class TestParseTable(unittest.TestCase):
    def test_parses_typed_values_and_empty_cells(self):
        payload = _table_payload(
            "CategoryCode,CategoryPchg,CategoryName,ParentName",
            ["a360|4.56|低空基建|", "081|-1.20|半导体|科技"],
        )
        parsed = theme._parse_table(payload["result"])
        self.assertEqual(parsed["total_pages"], 1)
        first = parsed["rows"][0]
        self.assertEqual(first["CategoryCode"], "a360")
        self.assertEqual(first["CategoryPchg"], 4.56)
        self.assertIsNone(first["ParentName"])
        self.assertEqual(parsed["rows"][1]["CategoryPchg"], -1.2)

    def test_handles_missing_or_bad_payload(self):
        self.assertEqual(theme._parse_table(None), {"rows": [], "total_pages": 0})
        self.assertEqual(theme._parse_table([]), {"rows": [], "total_pages": 0})

    def test_numeric_strings_keep_leading_zeros(self):
        payload = _table_payload(
            "SecuCode,CategoryCode,CategoryPchg,Importance",
            ["000584|003|1.23|0"],
        )
        row = theme._parse_table(payload["result"])["rows"][0]
        self.assertEqual(row["SecuCode"], "000584")
        self.assertEqual(row["CategoryCode"], "003")
        self.assertEqual(row["CategoryPchg"], 1.23)
        self.assertEqual(row["Importance"], 0)


class TestFetchEndpoints(unittest.TestCase):
    def test_today_opportunity_rows(self):
        payload = _table_payload(
            "CategoryPchg,CategoryName,Reason,SecuCode,SecuName",
            ["1.48|低空经济|低空经济国资入场|603153|上海建科"],
        )
        with patch.object(theme, "_fetch", return_value=payload) as fetch:
            rows = theme.today_opportunity()
        fetch.assert_called_once_with("todayopportunity")
        self.assertEqual(rows[0]["SecuName"], "上海建科")
        self.assertEqual(rows[0]["CategoryPchg"], 1.48)

    def test_hot_themes_uses_start_index_pagination(self):
        payload = _table_payload(
            "CategoryName,CategoryPchg", ["低空基建|4.56"], total_pages=34
        )
        with patch.object(theme, "_fetch", return_value=payload) as fetch:
            result = theme.hot_themes(page=3, page_size=20)
        fetch.assert_called_once_with(
            "hottheme", {"startIndex": 41, "pageSize": 20}
        )
        self.assertEqual(result["total_pages"], 34)
        self.assertEqual(result["rows"][0]["CategoryName"], "低空基建")

    def test_hot_stocks_passes_order_field(self):
        payload = _table_payload(
            "SecuCode,SecuName,Pchg", ["603019|中科曙光|5.21"]
        )
        with patch.object(theme, "_fetch", return_value=payload) as fetch:
            theme.hot_stocks(page_size=10)
        params = fetch.call_args.args[1]
        self.assertEqual(params["orderfield"], "Pchg")
        self.assertEqual(params["pageSize"], 10)

    def test_hot_stocks_rejects_bad_order_field(self):
        with self.assertRaises(ValueError):
            theme.hot_stocks(order_field="Pchg; DROP TABLE")
        with self.assertRaises(ValueError):
            theme.hot_stocks(order_field="a" * 33)

    def test_market_style_maps_days_to_paths(self):
        payload = {
            "re": True,
            "result": [
                {
                    "TopText": {"Title": "t", "Content": "c", "PositionInd": 87},
                    "MarketStyle": [
                        {"Category": "权重", "ThemeList": [{"Name": "水泥"}]}
                    ],
                }
            ],
        }
        with patch.object(theme, "_fetch", return_value=payload) as fetch:
            style = theme.market_style(days=3)
        fetch.assert_called_once_with("fengge/3")
        self.assertEqual(style["categories"][0]["category"], "权重")
        self.assertEqual(style["categories"][0]["themes"][0]["Name"], "水泥")

    def test_market_style_rejects_other_days(self):
        with self.assertRaises(ValueError):
            theme.market_style(days=2)

    def test_market_style_rejects_bool_days(self):
        with self.assertRaises(ValueError):
            theme.market_style(days=True)

    def test_validate_n_bounds(self):
        with self.assertRaises(ValueError):
            theme.hot_themes(page_size=0)
        with self.assertRaises(ValueError):
            theme.hot_themes(page_size=theme.MAX_THEME_COUNT + 1)
        with self.assertRaises(ValueError):
            theme.hot_themes(page=0)


class TestFetchErrors(unittest.TestCase):
    def test_fetch_wraps_request_exception(self):
        import requests

        class FakeSession:
            def get(self, *args, **kwargs):
                raise requests.RequestException("boom")

            def close(self):
                pass

        with patch.object(theme, "_session", return_value=FakeSession()):
            with self.assertRaises(RuntimeError) as ctx:
                theme._fetch("todayopportunity")
        self.assertIn("todayopportunity", str(ctx.exception))

    def test_fetch_rejects_re_false(self):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {"re": False, "message": "blocked"}

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

            def close(self):
                pass

        with patch.object(theme, "_session", return_value=FakeSession()):
            with self.assertRaises(RuntimeError) as ctx:
                theme._fetch("todayopportunity")
        self.assertIn("blocked", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
