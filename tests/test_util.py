"""stocks.util 单元测试。"""

import contextlib
import os
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

from stocks.util import force_direct_http


class TestForceDirectHttp(unittest.TestCase):
    def test_clears_proxy_env_and_forces_session_direct(self):
        os.environ["http_proxy"] = "http://127.0.0.1:7897"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
        seen = {}

        def fake_request(self, method, url, **kwargs):
            seen["proxies"] = kwargs.get("proxies")
            seen["http_proxy"] = os.environ.get("http_proxy")
            seen["HTTPS_PROXY"] = os.environ.get("HTTPS_PROXY")
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch.object(requests.Session, "request", fake_request):
            with force_direct_http():
                self.assertNotIn("http_proxy", os.environ)
                self.assertNotIn("HTTPS_PROXY", os.environ)
                self.assertEqual(os.environ.get("NO_PROXY"), "*")
                requests.Session().request("GET", "https://82.push2.eastmoney.com/")

        self.assertEqual(seen["proxies"], {"http": None, "https": None})
        self.assertIsNone(seen["http_proxy"])
        self.assertIsNone(seen["HTTPS_PROXY"])
        self.assertEqual(os.environ.get("http_proxy"), "http://127.0.0.1:7897")
        self.assertEqual(os.environ.get("HTTPS_PROXY"), "http://127.0.0.1:7897")

    def test_restores_env_and_request_on_exception(self):
        os.environ["http_proxy"] = "http://127.0.0.1:7897"
        original = requests.Session.request
        with self.assertRaises(RuntimeError):
            with force_direct_http():
                self.assertNotIn("http_proxy", os.environ)
                raise RuntimeError("boom")
        self.assertEqual(os.environ.get("http_proxy"), "http://127.0.0.1:7897")
        self.assertIs(requests.Session.request, original)

    def test_nested_same_thread_restores_once(self):
        os.environ["https_proxy"] = "http://127.0.0.1:7897"
        original = requests.Session.request
        with force_direct_http():
            with force_direct_http():
                self.assertNotIn("https_proxy", os.environ)
            self.assertNotIn("https_proxy", os.environ)
        self.assertEqual(os.environ.get("https_proxy"), "http://127.0.0.1:7897")
        self.assertIs(requests.Session.request, original)

    def test_concurrent_enter_exit_leaves_request_intact(self):
        original = requests.Session.request
        errors: list[BaseException] = []

        def worker():
            try:
                for _ in range(20):
                    with force_direct_http():
                        pass
            except BaseException as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        self.assertIs(requests.Session.request, original)


    def test_fetch_spot_uses_direct_http(self):
        from stocks import rank

        calls = []

        def fake_direct():
            calls.append("direct")
            return contextlib.nullcontext()

        with patch("stocks.rank.force_direct_http", side_effect=fake_direct):
            with patch(
                "stocks.rank._fetch_em_spot_board",
                side_effect=RuntimeError("down"),
            ):
                with self.assertRaises(ValueError):
                    rank._fetch_spot()
        self.assertEqual(calls, ["direct"])


if __name__ == "__main__":
    unittest.main()
