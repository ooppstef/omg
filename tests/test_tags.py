"""stocks.tags 会话级缓存与标签服务单测（mock 网络，不打真实请求）。"""

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pandas as pd

from stocks import tags


class TestTagsCache(unittest.TestCase):
    def setUp(self):
        tags.clear_tags_cache()

    def tearDown(self):
        tags.clear_tags_cache()

    def test_em_industries_cached_and_returns_copy(self):
        with patch(
            "stocks.tags._fetch_em_industries",
            return_value={"银行": "BK0475", "半导体": "BK1036"},
        ) as fetch:
            first = tags.em_industries()
            second = tags.em_industries()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first, second)
        # 修改返回对象不得污染缓存
        first["银行"] = "MUTATED"
        third = tags.em_industries()
        self.assertEqual(third["银行"], "BK0475")

    def test_em_constituents_cached_and_returns_new_list(self):
        with patch(
            "stocks.tags._fetch_em_constituents",
            return_value=[("600000", "浦发银行")],
        ) as fetch:
            first = tags.em_constituents("bk0475")
            second = tags.em_constituents("bk0475")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)  # 每次返回副本，非同一对象

    def test_hot_concept_names_keeps_full_cache_and_slices_top(self):
        names = [f"概念{i}" for i in range(tags.HOT_CONCEPT_TOP)]
        with patch("stocks.tags._fetch_hot_concept_names", return_value=names) as fetch:
            top5 = tags.hot_concept_names(top=5)
            top_all = tags.hot_concept_names()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(top5), 5)
        self.assertEqual(len(top_all), tags.HOT_CONCEPT_TOP)

    def test_clear_tags_cache_forces_refetch(self):
        with patch("stocks.tags._fetch_em_industries", return_value={"银行": "BK0475"}) as fetch:
            tags.em_industries()
            tags.clear_tags_cache()
            tags.em_industries()
        self.assertEqual(fetch.call_count, 2)

    def test_ttl_expiry_refetches(self):
        with patch(
            "stocks.tags._fetch_em_industries",
            side_effect=[{"银行": "BK0475"}, {"证券": "BK0473"}],
        ) as fetch:
            with patch.dict(
                __import__("os").environ,
                {"OMG_TAGS_CACHE_TTL_SECONDS": "1"},
            ):
                with patch(
                    "stocks.tags.time.monotonic",
                    side_effect=[100.0, 100.5, 102.0, 102.0, 102.0],
                ):
                    self.assertEqual(tags.em_industries(), {"银行": "BK0475"})
                    self.assertEqual(tags.em_industries(), {"银行": "BK0475"})
                    self.assertEqual(tags.em_industries(), {"证券": "BK0473"})
        self.assertEqual(fetch.call_count, 2)

    def test_constituent_cache_single_flights_concurrent_callers(self):
        def slow_fetch(_code):
            time.sleep(0.05)
            return [("600000", "浦发银行")]

        with patch(
            "stocks.tags._fetch_em_constituents",
            side_effect=slow_fetch,
        ) as fetch:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(tags.em_constituents, ["BK0475"] * 8))

        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(all(rows == [("600000", "浦发银行")] for rows in results))

    def test_concept_membership_single_flights_concurrent_callers(self):
        import adata

        def slow_fetch(*, index_code):
            self.assertEqual(index_code, "C1")
            time.sleep(0.05)
            return pd.DataFrame({"stock_code": ["600000"]})

        with patch.object(
            adata.stock.info,
            "concept_constituent_ths",
            side_effect=slow_fetch,
        ) as fetch:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(
                    pool.map(tags._concept_constituents_frame, ["C1"] * 8)
                )

        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(
            all(frame["stock_code"].tolist() == ["600000"] for frame in results)
        )

    def test_concept_catalog_cached_and_returns_copy(self):
        frame = pd.DataFrame([{"name": "机器人", "index_code": "C1"}])
        with patch("stocks.tags._fetch_concept_catalog", return_value=frame) as fetch:
            first = tags._concept_catalog_frame()
            first.loc[0, "name"] = "MUTATED"
            second = tags._concept_catalog_frame()

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(second.loc[0, "name"], "机器人")


class TestTagsService(unittest.TestCase):
    def setUp(self):
        tags.clear_tags_cache()

    def tearDown(self):
        tags.clear_tags_cache()

    def test_industry_by_code_empty_needed(self):
        self.assertEqual(tags.industry_by_code(set()), {})

    def test_industry_by_code_no_industries_returns_none(self):
        with patch("stocks.tags.em_industries", return_value={}):
            self.assertEqual(tags.industry_by_code({"600000"}), {"600000": None})

    def test_industry_by_code_maps_constituents(self):
        with patch("stocks.tags.em_industries", return_value={"银行": "BK0475"}) as em:
            with patch(
                "stocks.tags.em_constituents",
                return_value=[("600000", "浦发银行"), ("601398", "工商银行")],
            ):
                result = tags.industry_by_code({"600000", "601398", "300001"})
        self.assertEqual(result["600000"], "银行")
        self.assertEqual(result["601398"], "银行")
        self.assertIsNone(result["300001"])
        em.assert_called_once()

    def test_industry_by_code_lexicographic_min(self):
        with patch("stocks.tags.em_industries", return_value={"银行": "BK0475", "证券": "BK0473"}):
            with patch(
                "stocks.tags.em_constituents",
                side_effect=lambda bk: (
                    [("600000", "浦发")] if bk == "BK0475" else [("600000", "中信")]
                ),
            ):
                result = tags.industry_by_code({"600000"})
        # 多行业取字典序最小（Unicode 序：证券 < 银行）
        self.assertEqual(result["600000"], "证券")

    def test_concepts_by_code_empty(self):
        self.assertEqual(tags.concepts_by_code(set(), []), {})
        self.assertEqual(tags.concepts_by_code({"600000"}, []), {"600000": []})

    def test_concepts_by_code_injected_dependencies(self):
        concept_df = pd.DataFrame(
            [
                {"name": "机器人", "index_code": "C1"},
                {"name": "半导体", "index_code": "C2"},
            ]
        )

        def fake_constituent(index_code):
            return pd.DataFrame(
                {"stock_code": ["600000"] if index_code == "C1" else ["600000", "600001"]}
            )

        result = tags.concepts_by_code(
            {"600000", "600001"},
            ["机器人", "半导体"],
            concept_df=concept_df,
            fetch_constituent=fake_constituent,
        )
        self.assertEqual(result["600000"], ["机器人", "半导体"])
        self.assertEqual(result["600001"], ["半导体"])

    def test_em_industry_fetch_accepts_dict_diff(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "diff": {"0": {"f12": "BK0475", "f14": "银行"}}
                    }
                }

        class Session:
            def get(self, *_args, **_kwargs):
                return Response()

        with patch("stocks.tags._session", return_value=Session()):
            self.assertEqual(tags._fetch_em_industries(), {"银行": "BK0475"})

    def test_em_constituent_fetch_rejects_incomplete_pagination(self):
        class Response:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                return None

            def json(self):
                return {"data": self.data}

        class Session:
            page = 0

            def get(self, *_args, **_kwargs):
                self.page += 1
                if self.page == 1:
                    return Response(
                        {
                            "total": 150,
                            "diff": [
                                {"f12": f"600{i:03d}", "f14": f"股票{i}"}
                                for i in range(100)
                            ],
                        }
                    )
                return Response({"total": 150, "diff": []})

        with patch("stocks.tags._session", return_value=Session()):
            self.assertEqual(tags._fetch_em_constituents("BK0475"), [])


if __name__ == "__main__":
    unittest.main()
