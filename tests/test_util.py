"""stocks.util 单元测试。"""

import contextlib
import os
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

from stocks.util import fmt_chg, force_direct_http


class TestFmtChg(unittest.TestCase):
    def test_formats_finite_numeric_values(self):
        self.assertEqual(fmt_chg(1.234), "+1.23%")
        self.assertEqual(fmt_chg("-0.5"), "-0.50%")

    def test_rejects_nonfinite_and_nonnumeric_values(self):
        for value in (
            float("nan"),
            float("inf"),
            float("-inf"),
            None,
            "bad",
            True,
        ):
            with self.subTest(value=value):
                self.assertEqual(fmt_chg(value), "-")


class TestForceDirectHttp(unittest.TestCase):
    def test_keeps_proxy_env_and_forces_session_direct(self):
        proxy = "http://127.0.0.1:7897"
        seen = {}

        def fake_send(self, request, **kwargs):
            seen["proxies"] = kwargs.get("proxies")
            seen["timeout"] = kwargs.get("timeout")
            seen["http_proxy"] = os.environ.get("http_proxy")
            seen["HTTPS_PROXY"] = os.environ.get("HTTPS_PROXY")
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch.dict(
            os.environ,
            {"http_proxy": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy},
            clear=True,
        ):
            with patch.object(requests.Session, "send", fake_send):
                with force_direct_http():
                    self.assertEqual(os.environ.get("http_proxy"), proxy)
                    self.assertEqual(os.environ.get("HTTPS_PROXY"), proxy)
                    requests.Session().get("https://82.push2.eastmoney.com/")

        self.assertEqual(dict(seen["proxies"]), {"http": "", "https": "", "all": ""})
        self.assertEqual(seen["timeout"], 15)
        self.assertEqual(seen["http_proxy"], proxy)
        self.assertEqual(seen["HTTPS_PROXY"], proxy)

    def test_restores_env_and_request_on_exception(self):
        proxy = "http://127.0.0.1:7897"
        with patch.dict(os.environ, {"http_proxy": proxy}, clear=True):
            original = requests.Session.request
            with self.assertRaises(RuntimeError):
                with force_direct_http():
                    self.assertEqual(os.environ.get("http_proxy"), proxy)
                    raise RuntimeError("boom")
            self.assertEqual(os.environ.get("http_proxy"), proxy)
            self.assertIs(requests.Session.request, original)

    def test_nested_same_thread_restores_once(self):
        proxy = "http://127.0.0.1:7897"
        with patch.dict(os.environ, {"https_proxy": proxy}, clear=True):
            original = requests.Session.request
            with force_direct_http():
                with force_direct_http():
                    self.assertEqual(os.environ.get("https_proxy"), proxy)
                self.assertEqual(os.environ.get("https_proxy"), proxy)
            self.assertEqual(os.environ.get("https_proxy"), proxy)
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

    def test_concurrent_direct_requests_are_not_serialized(self):
        rendezvous = threading.Barrier(2)
        errors: list[BaseException] = []

        def fake_request(self, method, url, **kwargs):
            rendezvous.wait(timeout=1)
            return MagicMock(status_code=200)

        def worker():
            try:
                with force_direct_http():
                    requests.Session().request("GET", "https://example.com/")
            except BaseException as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

        with patch.object(requests.Session, "request", fake_request):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())

        self.assertEqual(errors, [])

    def test_context_does_not_disable_proxy_in_another_thread(self):
        proxy = "http://127.0.0.1:7897"
        entered = threading.Event()
        release = threading.Event()
        seen = {}

        def holder():
            with force_direct_http():
                entered.set()
                release.wait(timeout=3)

        def fake_send(self, request, **kwargs):
            seen["proxies"] = kwargs.get("proxies")
            return MagicMock(status_code=200)

        with patch.dict(os.environ, {"HTTPS_PROXY": proxy}, clear=True):
            with patch.object(requests.Session, "send", fake_send):
                thread = threading.Thread(target=holder)
                thread.start()
                self.assertTrue(entered.wait(timeout=1))
                requests.Session().get("https://example.com/")
                release.set()
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())

        self.assertEqual(seen["proxies"].get("https"), proxy)


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
