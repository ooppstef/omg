"""stocks.wencai 单元测试（mock 请求与 token，不打网络）。"""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from stocks import wencai


def _payload(status=0, rows=None, columns=None, text_answer=""):
    return {
        "status_code": status,
        "status_msg": "ok" if status == 0 else "boom",
        "data": {
            "answer": [
                {
                    "text_answer": text_answer,
                    "txt": [
                        {
                            "content": {
                                "components": [
                                    {
                                        "show_type": "xuangu_tableV1",
                                        "data": {
                                            "columns": [
                                                {"key": key} for key in (columns or [])
                                            ],
                                            "datas": rows or [],
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                }
            ]
        },
    }


class TestParseRobotPayload(unittest.TestCase):
    def test_extracts_first_table_and_text(self):
        payload = _payload(
            rows=[
                {"股票代码": "000099", "股票简称": "中信海直", "code": "000099"},
                {"股票代码": "000420", "股票简称": "吉林化纤", "code": "000420"},
            ],
            columns=["股票代码", "股票简称", "code"],
            text_answer="为您找到2条结果",
        )
        parsed = wencai._parse_robot_payload(payload)
        self.assertEqual(parsed["count"], 2)
        self.assertEqual(parsed["rows"][0]["股票简称"], "中信海直")
        self.assertEqual(parsed["columns"], ["股票代码", "股票简称", "code"])
        self.assertEqual(parsed["text_answer"], "为您找到2条结果")

    def test_nonzero_status_raises(self):
        with self.assertRaises(RuntimeError):
            wencai._parse_robot_payload(_payload(status=-302))

    def test_empty_answer_yields_empty_rows(self):
        parsed = wencai._parse_robot_payload(
            {"status_code": 0, "status_msg": "ok", "data": {"answer": []}}
        )
        self.assertEqual(parsed["rows"], [])
        self.assertEqual(parsed["count"], 0)

    def test_parser_ignores_malformed_items(self):
        payload = {
            "status_code": 0,
            "data": {
                "answer": [
                    {
                        "text_answer": "x",
                        "txt": [
                            "not-a-dict",
                            {
                                "content": {
                                    "components": [
                                        "bad",
                                        {
                                            "show_type": "xuangu_tableV1",
                                            "data": {
                                                "columns": ["bad", {"key": "股票代码"}],
                                                "datas": ["bad", {"股票代码": "1"}],
                                            },
                                        },
                                    ]
                                }
                            },
                        ],
                    }
                ]
            },
        }
        parsed = wencai._parse_robot_payload(payload)
        self.assertEqual(parsed["columns"], ["股票代码"])
        self.assertEqual(parsed["rows"], [{"股票代码": "1"}])


class TestQuery(unittest.TestCase):
    def test_query_orchestration_and_form(self):
        captured = {}

        def fake_post(form, cookie=None):
            captured["form"] = form
            captured["cookie"] = cookie
            return _payload(
                rows=[{"股票代码": "000099"}],
                columns=["股票代码"],
                text_answer="共1条",
            )

        with patch.object(wencai, "_post_form", side_effect=fake_post):
            result = wencai.query("今日涨停", perpage=50)

        self.assertEqual(result["question"], "今日涨停")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["perpage"], 50)
        self.assertEqual(captured["form"]["question"], "今日涨停")
        self.assertEqual(captured["form"]["perpage"], "50")
        self.assertEqual(captured["form"]["page"], "1")
        self.assertEqual(captured["form"]["source"], "Ths_iwencai_Xuangu")

    def test_query_cookie_falls_back_to_env(self, ):
        captured = {}

        def fake_post(form, cookie=None):
            captured["cookie"] = cookie
            return _payload()

        with patch.object(wencai, "_post_form", side_effect=fake_post), patch.dict(
            "os.environ", {"OMG_WENCAI_COOKIE": "v=abc; other_uid=1"}
        ):
            wencai.query("今日涨停")
        self.assertEqual(captured["cookie"], "v=abc; other_uid=1")

    def test_query_explicit_cookie_beats_env(self):
        captured = {}

        def fake_post(form, cookie=None):
            captured["cookie"] = cookie
            return _payload()

        with patch.object(wencai, "_post_form", side_effect=fake_post), patch.dict(
            "os.environ", {"OMG_WENCAI_COOKIE": "env=1"}
        ):
            wencai.query("今日涨停", cookie="explicit=1")
        self.assertEqual(captured["cookie"], "explicit=1")

    def test_query_loads_cookie_from_dotenv_when_env_empty(self):
        captured = {}

        def fake_post(form, cookie=None):
            captured["cookie"] = cookie
            return _payload()

        def fake_load_dotenv():
            os.environ["OMG_WENCAI_COOKIE"] = "dotenv=1"

        saved = os.environ.pop("OMG_WENCAI_COOKIE", None)
        try:
            with patch.object(
                wencai, "_post_form", side_effect=fake_post
            ), patch(
                "stocks.util.load_dotenv", side_effect=fake_load_dotenv
            ):
                wencai.query("今日涨停")
        finally:
            os.environ.pop("OMG_WENCAI_COOKIE", None)
            if saved is not None:
                os.environ["OMG_WENCAI_COOKIE"] = saved
        self.assertEqual(captured["cookie"], "dotenv=1")

    def test_query_validates_question_and_perpage(self):
        with self.assertRaises(ValueError):
            wencai.query("   ")
        with self.assertRaises(ValueError):
            wencai.query("今日涨停", perpage=0)
        with self.assertRaises(ValueError):
            wencai.query("今日涨停", perpage=wencai.MAX_PERPAGE + 1)
        with self.assertRaises(ValueError):
            wencai.query("今日涨停", page=0)


class TestPostFormErrors(unittest.TestCase):
    @staticmethod
    def _fake_session(status_code=200, payload=None, raise_exc=None):
        class FakeResponse:
            def __init__(self):
                self.status_code = status_code

            def json(self):
                if payload is None:
                    raise ValueError("no json")
                return payload

        class FakeSession:
            def post(self, *args, **kwargs):
                if raise_exc is not None:
                    raise raise_exc
                return FakeResponse()

            def close(self):
                pass

        return FakeSession()

    def test_403_mentions_cookie_fallback(self):
        with patch.object(wencai, "_hexin_token", return_value="T"), patch.object(
            wencai, "_session", return_value=self._fake_session(status_code=403)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                wencai._post_form({"question": "x"})
        self.assertIn("OMG_WENCAI_COOKIE", str(ctx.exception))

    def test_non_json_raises_login_wall_hint(self):
        with patch.object(wencai, "_hexin_token", return_value="T"), patch.object(
            wencai, "_session", return_value=self._fake_session(payload=None)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                wencai._post_form({"question": "x"})
        self.assertIn("非 JSON", str(ctx.exception))

    def test_request_exception_wrapped(self):
        import requests

        with patch.object(wencai, "_hexin_token", return_value="T"), patch.object(
            wencai,
            "_session",
            return_value=self._fake_session(
                raise_exc=requests.RequestException("boom")
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                wencai._post_form({"question": "x"})
        self.assertIn("问财请求失败", str(ctx.exception))


class TestFormatCell(unittest.TestCase):
    def test_long_decimal_strings_are_compacted(self):
        self.assertEqual(wencai._fmt_cell("9.99254287844892"), "9.99254")
        self.assertEqual(wencai._fmt_cell(10.098522167487), "10.0985")

    def test_large_numbers_use_thousands_separator(self):
        self.assertEqual(wencai._fmt_cell(11442609520.75), "11,442,609,521")
        self.assertEqual(wencai._fmt_cell("11442609520.75"), "11,442,609,521")
        # :g 临界舍入成科学计数法的边界值也要回退
        self.assertEqual(wencai._fmt_cell(999999.9), "1,000,000")
        self.assertEqual(wencai._fmt_cell(500000.0), "500000")

    def test_plain_numeric_strings_keep_leading_zeros(self):
        self.assertEqual(wencai._fmt_cell("000584"), "000584")
        self.assertEqual(wencai._fmt_cell("000099.SZ"), "000099.SZ")
        self.assertEqual(wencai._fmt_cell("14.75"), "14.75")

    def test_none_and_container_values(self):
        self.assertEqual(wencai._fmt_cell(None), "-")
        self.assertEqual(wencai._fmt_cell([1, 2]), "(数据)")


class TestDisplayWidth(unittest.TestCase):
    def test_display_width_counts_cjk_as_two_columns(self):
        self.assertEqual(wencai._display_width("abc"), 3)
        self.assertEqual(wencai._display_width("中文"), 4)
        self.assertEqual(wencai._display_width("中信海直"), 8)

    def test_fit_truncates_by_display_width(self):
        self.assertEqual(wencai._fit("abc", 3), "abc")
        self.assertEqual(wencai._fit("中文测试", 6), "中文…")

    def test_pad_fills_to_display_width(self):
        self.assertEqual(wencai._pad("中文", 6), "中文  ")
        self.assertEqual(wencai._pad("ab", 2), "ab")


class TestPrintQueryResult(unittest.TestCase):
    def test_renders_table_without_error(self):
        result = {
            "text_answer": "为您找到1条结果",
            "columns": ["股票代码", "股票简称", "market_code", "涨停时间明细[20261016]"],
            "rows": [
                {
                    "股票代码": "000099",
                    "股票简称": "中信海直",
                    "market_code": "33",
                    "涨停时间明细[20261016]": "09:30:00|| 10:00:00",
                }
            ],
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wencai.print_query_result(result)
        out = buf.getvalue()
        self.assertIn("为您找到1条结果", out)
        self.assertIn("中信海直", out)
        self.assertNotIn("market_code", out)
        self.assertNotIn("明细", out)


if __name__ == "__main__":
    unittest.main()
