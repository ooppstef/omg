"""stocks.strength 单元测试：注入原始 f 字段行，全部离线（不打网络）。"""

import unittest
from unittest import mock

import pandas as pd

from stocks import strength

# 2026-10-16 15:39:30 +08:00（东财 f124 实测值）
_TS = 1792136370
_DAY = "2026-10-16"
_BENCHMARK = {
    "ret3_hs300": -2.46,
    "ret5_hs300": -3.4,
    "ret10_hs300": -6.05,
    "ret60_hs300": -11.23,
}


def _board(code: str, name: str, **fields) -> dict:
    row = {
        "f12": code,
        "f13": 90,
        "f14": name,
        "f2": 1000.0,
        "f3": 0.0,
        "f6": 1e10,
        "f8": 1.0,
        "f10": 1.0,
        "f24": 0.0,
        "f25": 0.0,
        "f104": 10,
        "f105": 10,
        "f109": 0.0,
        "f124": _TS,
        "f127": 0.0,
        "f128": "领涨股",
        "f136": 5.0,
        "f140": "600000",
        "f160": 0.0,
    }
    row.update(fields)
    return row


def _stock(code: str, name: str, **fields) -> dict:
    row = {
        "f12": code,
        "f13": 0,
        "f14": name,
        "f2": 10.0,
        "f3": 0.0,
        "f6": 1e8,
        "f8": 3.0,
        "f10": 1.2,
        "f24": 0.0,
        "f109": 0.0,
        "f124": _TS,
        "f127": 0.0,
        "f160": 0.0,
    }
    row.update(fields)
    return row


def _tech(rel_vol=1.5, **fields) -> dict:
    data = {
        "as_of": _DAY,
        "source": "sina",
        "close": 12.0,
        "rel_vol": rel_vol,
        "rv20": 1.2,
        "vs_ma5_pct": 6.0,
        "vs_ma20_pct": 9.0,
        "vs_ma60_pct": 12.0,
        "adx": 32.0,
        "macd_hist": 0.4,
        "dif": 0.5,
        "dea": 0.3,
        "close_loc": 0.9,
        "natr": 5.0,
        "vol_stance": "明显放量",
    }
    data.update(fields)
    return data


def _score(rows: list) -> list:
    """score_boards 吃归一化记录（analyze 内部先跑 board_record），测试里显式归一化。"""
    return strength.score_boards([strength.board_record(row) for row in rows])


def _weak_concepts(n: int = 20) -> list:
    return [
        _board(
            f"BK9{i:03d}",
            f"弱势概念{i}",
            f3=-2.0,
            f127=-2.5,
            f109=-8.0 - i * 0.2,
            f160=-12.0,
            f24=-6.0,
            f104=5,
            f105=45,
        )
        for i in range(n)
    ]


class TestQuoteDate(unittest.TestCase):
    def test_derive_quote_date_uses_mode_and_latest_stamp(self):
        records = [{"quote_ts": _TS}, {"quote_ts": _TS - 60}, {"quote_ts": _TS - 86400}]
        day, moment = strength.derive_quote_date(records)
        self.assertEqual(day, _DAY)
        self.assertEqual(moment, "2026-10-16T15:39:30+08:00")

    def test_derive_quote_date_ignores_missing_and_bogus_stamps(self):
        self.assertEqual(strength.derive_quote_date([]), (None, None))
        self.assertEqual(
            strength.derive_quote_date([{"quote_ts": None}, {"quote_ts": 12}]),
            (None, None),
        )


class TestRecords(unittest.TestCase):
    def test_board_record_computes_breadth(self):
        item = strength.board_record(
            _board("BK1", "题材", f104=30, f105=10, f109=3.5, f24=9.0)
        )
        self.assertEqual(item["code"], "BK1")
        self.assertEqual(item["ret5"], 3.5)
        self.assertEqual(item["ret60"], 9.0)
        self.assertAlmostEqual(item["breadth"], 0.75, places=6)
        self.assertEqual(item["leader"], "领涨股")

    def test_board_record_rejects_incomplete_rows(self):
        self.assertIsNone(strength.board_record({"f12": "", "f14": "空代码"}))
        self.assertIsNone(strength.board_record({"f12": "BK1", "f14": ""}))
        self.assertIsNone(strength.board_record("not-a-dict"))

    def test_stock_record_maps_multi_period_returns(self):
        item = strength.stock_record(
            _stock("600000", "示例", f127=1.0, f109=2.0, f160=3.0, f24=4.0)
        )
        self.assertEqual(
            (item["ret3"], item["ret5"], item["ret10"], item["ret60"]),
            (1.0, 2.0, 3.0, 4.0),
        )


class TestUniverseFilters(unittest.TestCase):
    def test_is_theme_concept_drops_pseudo_concepts(self):
        dropped = [
            "昨日涨停",
            "昨日连板_含一字",
            "2026三季报预减",
            "证金持股",
            "红利破净股",
            "高送转",
            "融资融券",
            "沪深300样本股",
            "ST股",
            "",
        ]
        kept = [
            "AI语料",
            "数字阅读",
            "知识产权",
            "航天航空",
            "飞行汽车(eVTOL)",
            "兵装重组",
            "CPO概念",
        ]
        for name in dropped:
            with self.subTest(name=name):
                self.assertFalse(strength._is_theme_concept(name))
        for name in kept:
            with self.subTest(name=name):
                self.assertTrue(strength._is_theme_concept(name))

    def test_is_new_listing_matches_prefix_only_for_chinese_names(self):
        self.assertTrue(strength._is_new_listing("N华虹"))
        self.assertTrue(strength._is_new_listing("C联亚"))
        self.assertFalse(strength._is_new_listing("慈文传媒"))
        self.assertFalse(strength._is_new_listing("TCL科技"))
        self.assertFalse(strength._is_new_listing("CXO样本"))
        self.assertFalse(strength._is_new_listing(""))

    def test_is_a_share_uses_shared_prefixes(self):
        self.assertTrue(strength._is_a_share("600000"))
        self.assertTrue(strength._is_a_share("301052"))
        self.assertTrue(strength._is_a_share("688001"))
        self.assertFalse(strength._is_a_share("920006"))
        self.assertFalse(strength._is_a_share("830001"))
        self.assertFalse(strength._is_a_share(""))

    def test_dedupe_industry_lines_keeps_highest_line_per_key(self):
        boards = [
            {"name": "大众出版", "score": 99.9},
            {"name": "出版", "score": 99.7},
            {"name": "教育出版", "score": 99.5},
            {"name": "地面兵装Ⅱ", "score": 98.8},
            {"name": "地面兵装Ⅲ", "score": 98.7},
            {"name": "银行", "score": 90.0},
        ]
        kept = [b["name"] for b in strength._dedupe_industry_lines(boards)]
        self.assertEqual(kept, ["大众出版", "教育出版", "地面兵装Ⅱ", "银行"])


class TestWeightedZ(unittest.TestCase):
    """_weighted_z 归一化：相对权重（和不需恰好为 1）、缺列只降权、排序不变。"""

    ROWS = [
        {"code": "600001", "ret5": 20.0, "ret10": 25.0, "vol_ratio": 2.2},
        {"code": "600002", "ret5": 6.0, "ret10": 7.0, "vol_ratio": 1.1},
        {"code": "600003", "ret5": -3.0, "ret10": -5.0, "vol_ratio": 0.7},
    ]

    def test_scale_invariant_normalized_by_present_keys(self):
        frame = pd.DataFrame(self.ROWS)
        base = strength._weighted_z(frame, (("ret5", 0.40), ("ret10", 0.30), ("vol_ratio", 0.20)))
        doubled = strength._weighted_z(frame, (("ret5", 0.80), ("ret10", 0.60), ("vol_ratio", 0.40)))
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(base, doubled)))

    def test_missing_key_only_downweights(self):
        frame = pd.DataFrame(self.ROWS)
        partial = strength._weighted_z(frame, (("ret5", 0.4), ("ghost", 9.0)))
        expect = strength._robust_z(frame["ret5"])
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(partial, expect)))

    def test_order_preserved_when_key_missing(self):
        frame = pd.DataFrame(self.ROWS)
        base = list(strength._weighted_z(frame, (("ret5", 0.4), ("ret10", 0.3), ("vol_ratio", 0.2))))
        partial = list(strength._weighted_z(frame, (("ret5", 0.4), ("ret10", 0.3))))
        self.assertEqual(base, sorted(base, reverse=True))
        self.assertEqual(partial, sorted(partial, reverse=True))

    def test_all_keys_missing_returns_zero_series(self):
        frame = pd.DataFrame(self.ROWS)
        zeros = strength._weighted_z(frame, (("ghost", 1.0),))
        self.assertEqual([0.0, 0.0, 0.0], [float(v) for v in zeros])


class TestFetchDegradation(unittest.TestCase):
    """市场级取数页失败：部分行保留 + errors 记录（不再静默）。"""

    @staticmethod
    def _fake_em_page(fail_from_page: int):
        def fake(fs, *, fields, fid, pn, **kwargs):
            if pn >= fail_from_page:
                raise RuntimeError("page down")
            code = "600001" if fs == strength.FS_SH else "000001"
            return [{"f12": code, "f14": "甲"}], 100

        return fake

    def test_market_scan_partial_rows_kept_and_error_recorded(self):
        errors: list = []
        with mock.patch("stocks.strength._em_page", side_effect=self._fake_em_page(2)):
            rows = strength.fetch_market_scan(errors)
        self.assertEqual([r["f12"] for r in rows], ["600001", "000001"])
        scan_errors = [e for e in errors if e.startswith("bottom_scan:")]
        self.assertEqual(len(scan_errors), 2)
        self.assertIn("RuntimeError", scan_errors[0])

    def test_market_scan_all_fail_returns_empty_without_errors(self):
        with mock.patch("stocks.strength._em_page", side_effect=self._fake_em_page(1)):
            self.assertEqual(strength.fetch_market_scan(), [])

    def test_market_top_all_fail_recorded(self):
        errors: list = []

        def fake(fs, *, fields, fid, pn, **kwargs):
            raise RuntimeError("down")

        with mock.patch("stocks.strength._em_page", side_effect=fake):
            rows = strength.fetch_market_top(errors)
        self.assertEqual(rows, [])
        top_errors = [e for e in errors if e.startswith("market_top:")]
        self.assertEqual(len(top_errors), 4)
        self.assertIn("f109", top_errors[0])

    def test_market_top_partial_kept(self):
        errors: list = []

        def fake(fs, *, fields, fid, pn, **kwargs):
            if fid == "f160":
                raise RuntimeError("down")
            code = "600001" if fs == strength.FS_SH else "000001"
            return [{"f12": code, "f14": "甲"}], 42

        with mock.patch("stocks.strength._em_page", side_effect=fake):
            rows = strength.fetch_market_top(errors)
        self.assertEqual([r["f12"] for r in rows], ["600001", "000001"])
        self.assertEqual(len([e for e in errors if e.startswith("market_top:")]), 2)


class TestScoring(unittest.TestCase):
    def test_score_boards_percentile_is_monotonic_and_below_100(self):
        rows = [
            _board(f"BK{i:04d}", f"题材{i}", f109=float(12 - i))
            for i in range(25)
        ]
        scored = _score(rows)
        self.assertEqual(len(scored), 25)
        scores = [item["score"] for item in scored]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertLess(scores[0], 100.0)
        self.assertGreater(scores[-1], 0.0)

    def test_score_boards_turn_score_rewards_fresh_strength(self):
        fresh = _board("BK9001", "刚转强", f3=9.5, f127=6.0, f109=-8.0, f160=-12.0)
        aging = _board("BK9002", "旧强势", f3=-1.0, f127=-1.0, f109=9.0, f160=12.0)
        filler = [
            _board(f"BK8{i:03d}", f"普通{i}", f3=-2.0, f127=-2.0, f109=-4.0)
            for i in range(22)
        ]
        scored = {item["code"]: item for item in _score([fresh, aging, *filler])}
        self.assertGreater(scored["BK9001"]["turn_score"], scored["BK9002"]["turn_score"])

    def test_breadth_and_stage_labels(self):
        self.assertEqual(strength._breadth_label(0.9), strength.BREADTH_WIDE)
        self.assertEqual(strength._breadth_label(0.5), strength.BREADTH_MIXED)
        self.assertEqual(strength._breadth_label(0.2), strength.BREADTH_NARROW)
        self.assertIsNone(strength._breadth_label(None))
        self.assertEqual(strength._stage_of(1.0, 1.0, 1.0), strength.STAGE_LEAD)
        self.assertEqual(strength._stage_of(0.0, 1.0, 1.0), strength.STAGE_COOL)
        self.assertEqual(strength._stage_of(-2.0, -2.0, -2.0), strength.STAGE_EBB)
        self.assertEqual(strength._stage_of(None, None, None), strength.STAGE_FLAT)

    def test_stock_stage_uses_absolute_excess_pp(self):
        self.assertEqual(strength._stock_stage(8.0, 20.0, 25.0), strength.STAGE_LEAD)
        self.assertEqual(strength._stock_stage(-3.0, 20.0, 25.0), strength.STAGE_COOL)
        self.assertEqual(strength._stock_stage(5.0, 1.0, 0.0), strength.STAGE_WARM)
        self.assertEqual(strength._stock_stage(-6.0, -8.0, -9.0), strength.STAGE_EBB)
        self.assertEqual(strength._stock_stage(0.0, 0.0, 1.0), strength.STAGE_FLAT)
        self.assertEqual(strength._stock_stage(None, None, None), strength.STAGE_FLAT)

    def test_momentum_key_prefers_fresh_limit_up(self):
        fresh = {"ret5": 1.0, "ret3": 7.0, "change_pct": 20.0}
        laggard = {"ret5": 9.0, "ret3": 0.0, "change_pct": 0.0}
        self.assertGreater(strength._momentum_key(fresh), strength._momentum_key(laggard))
        self.assertEqual(strength._momentum_key({}), -999.0)


class TestSentiment(unittest.TestCase):
    def test_sentiment_from_zt_computes_hhi_height_and_first_board(self):
        rows = [
            {"代码": "600000", "名称": "A", "所属行业": "汽车零部", "连板数": 11},
            {"代码": "600001", "名称": "B", "所属行业": "汽车零部", "连板数": 2},
            {"代码": "600002", "名称": "C", "所属行业": "汽车零部", "连板数": 1},
            {"代码": "600003", "名称": "D", "所属行业": "出版", "连板数": 1},
            {"代码": "600004", "名称": "E", "所属行业": "出版", "连板数": 1},
        ]
        sent = strength._sentiment_from_zt(rows)
        self.assertTrue(sent["ok"])
        self.assertEqual(sent["limit_up"], 5)
        self.assertEqual(sent["height"], 11)
        self.assertEqual(sent["first_board_ratio"], 0.6)
        self.assertEqual(sent["max_industry"], "汽车零部")
        self.assertAlmostEqual(sent["industry_hhi"], 0.52, places=4)
        self.assertAlmostEqual(sent["max_industry_share"], 0.6, places=6)

    def test_sentiment_from_zt_empty(self):
        self.assertEqual(strength._sentiment_from_zt([]), {"ok": False, "limit_up": 0})


class TestRegime(unittest.TestCase):
    def test_theme_huddle_beats_defensive_style(self):
        concepts = [
            *[_board(f"BK{i:03d}", f"弱{i}", f109=-5.0, f3=-1.0, f104=2, f105=8) for i in range(22)],
            *[_board(f"BK9{i:02d}", f"强{i}", f109=10.0, f3=3.0, f104=8, f105=2) for i in range(3)],
        ]
        regime = strength.compute_regime(
            _score(concepts),
            sentiment={
                "ok": True,
                "limit_up": 61,
                "height": 11,
                "first_board_ratio": 0.885,
                "industry_hhi": 0.0857,
                "max_industry": "汽车零部",
                "max_industry_share": 0.23,
            },
            style_state="红利防守",
        )
        self.assertEqual(regime["regime"], strength.REGIME_HUDDLE)
        self.assertEqual(regime["confidence"], "高")
        self.assertEqual(regime["metrics"]["style_state"], "红利防守")

    def test_down_regime_when_no_cluster(self):
        concepts = [
            _board(f"BK{i:03d}", f"弱{i}", f109=-7.0, f127=-2.0, f3=-2.0, f104=2, f105=8)
            for i in range(25)
        ]
        regime = strength.compute_regime(
            _score(concepts),
            sentiment={"ok": True, "limit_up": 12, "industry_hhi": 0.01},
            style_state=None,
        )
        self.assertEqual(regime["regime"], strength.REGIME_DOWN)
        self.assertIn("概念 5 日上涨占比 0%", regime["reasons"][0])

    def test_defensive_regime_in_narrow_dispersion_window(self):
        concepts = [
            _board(f"BK{i:03d}", f"弱{i}", f109=-6.0, f127=-2.0, f3=-1.0, f104=2, f105=8)
            for i in range(24)
        ]
        concepts.append(
            _board("BK999", "略强", f109=-5.9, f127=-2.0, f3=-1.0, f104=2, f105=8)
        )
        regime = strength.compute_regime(
            _score(concepts),
            sentiment={"ok": True, "limit_up": 30, "industry_hhi": 0.02},
            style_state="红利防守",
        )
        self.assertEqual(regime["regime"], strength.REGIME_DEFENSIVE)

    def test_broad_regime_when_diffusion(self):
        concepts = [
            *[_board(f"BK{i:03d}", f"强{i}", f109=5.0, f3=1.0, f104=8, f105=2) for i in range(22)],
            *[_board(f"BK9{i:02d}", f"普通{i}", f109=2.0, f3=0.5, f104=6, f105=4) for i in range(3)],
        ]
        regime = strength.compute_regime(_score(concepts), sentiment=None, style_state=None)
        self.assertEqual(regime["regime"], strength.REGIME_BROAD)

    def test_unknown_regime_when_sample_too_small(self):
        regime = strength.compute_regime(
            _score([_board(f"BK{i}", f"题材{i}", f109=float(i)) for i in range(5)])
        )
        self.assertEqual(regime["regime"], strength.REGIME_UNKNOWN)
        self.assertEqual(regime["confidence"], "低")


class TestClustering(unittest.TestCase):
    def test_cluster_themes_merges_overlapping_boards(self):
        boards = [
            {"code": "BK1", "name": "概念A", "score": 90.0},
            {"code": "BK2", "name": "行业B", "score": 80.0},
            {"code": "BK3", "name": "无关C", "score": 70.0},
        ]
        member_sets = {
            "BK1": {f"60{i:04d}" for i in range(10)},
            "BK2": {f"60{i:04d}" for i in range(9)} | {"600099"},
            "BK3": {f"00{i:04d}" for i in range(8)},
        }
        clusters = strength.cluster_themes(boards, member_sets)
        self.assertEqual(len(clusters), 2)
        merged = clusters[0]
        self.assertEqual(merged["primary"]["code"], "BK1")
        self.assertEqual(merged["board_codes"], ["BK1", "BK2"])
        self.assertEqual(merged["aliases"], ["行业B"])
        self.assertEqual(len(merged["member_codes"]), 11)

    def test_cluster_themes_without_members_returns_single_boards(self):
        boards = [
            {"code": "BK1", "name": "A", "score": 1.0},
            {"code": "BK2", "name": "B", "score": 2.0},
        ]
        clusters = strength.cluster_themes(boards, {})
        self.assertEqual(len(clusters), 2)

    def test_jaccard_handles_empty_sets(self):
        self.assertEqual(strength._jaccard(set(), {"a"}), 0.0)
        self.assertEqual(strength._jaccard({"a"}, {"a"}), 1.0)

    def test_cluster_threshold_matches_measured_real_overlap(self):
        """阈值按实测校准：真实同源线 0.13~0.19 会合并，伪相关 0.04 不合并。"""
        broad = [{"code": "BK1", "name": "大盘块", "score": 99.0}]
        broad_set = {f"600{i:03d}" for i in range(60)}
        member_sets = {"BK1": broad_set}
        # 同源小板块：命中 9 只（9/61 ≈ 0.15 ≥ 0.12）→ 合并
        boards = list(broad)
        member_sets["BK2"] = {f"600{i:03d}" for i in range(9)} | {"000001"}
        boards.append({"code": "BK2", "name": "同源小板块", "score": 98.0})
        # 伪相关小板块：命中 3 只（3/69 ≈ 0.04 < 0.12），且不与 BK2 重叠 → 不合并
        member_sets["BK3"] = {"600050", "600051", "600052"} | {f"300{i:03d}" for i in range(9)}
        boards.append({"code": "BK3", "name": "伪相关小板块", "score": 97.0})
        clusters = strength.cluster_themes(boards, member_sets)
        groups = {c["primary"]["code"]: sorted(c["board_codes"]) for c in clusters}
        self.assertEqual(groups["BK1"], ["BK1", "BK2"])
        self.assertEqual(groups["BK3"], ["BK3"])


class TestThemePayload(unittest.TestCase):
    def _cluster(self):
        return {
            "primary": {
                "code": "BK0480",
                "name": "航天航空",
                "score": 99.3,
                "turn_score": 99.9,
                "picked_by": "概念强度分 Top8",
                "stage": strength.STAGE_LEAD,
                "breadth": 0.93,
                "breadth_label": strength.BREADTH_WIDE,
                "ret3": 0.48,
                "ret5": 0.77,
                "ret10": -3.91,
                "ret60": 0.04,
                "vol_ratio": 1.66,
            },
            "board_codes": ["BK0480"],
            "aliases": [],
            "member_codes": {"300581", "300045", "920006"},
        }

    def test_theme_payload_strong_member_view(self):
        members = {
            "BK0480": [
                _stock("300581", "晨曦航空", f3=20.02, f127=12.0, f109=19.53, f160=21.0, f24=29.64),
                _stock("300045", "华力创通", f3=20.04, f127=7.5, f109=-0.31, f160=8.92, f24=-18.49),
                _stock("920006", "北交所样本", f3=29.98, f109=31.27),
                _stock("600001", "ST样本", f3=5.0, f109=12.0),
            ]
        }
        theme = strength.theme_payload(
            self._cluster(),
            members,
            zt_codes={"300581", "300045"},
            benchmark={"ret5_hs300": -3.4},
        )
        self.assertEqual(theme["name"], "航天航空")
        self.assertEqual(theme["n_members"], 2)  # 北交所与 ST 被剔除
        self.assertEqual(theme["limit_up_count"], 2)
        self.assertAlmostEqual(theme["excess5"], 4.17, places=2)
        self.assertEqual([leader["code"] for leader in theme["leaders"]], ["300581", "300045"])
        self.assertIn("强势成员 2 只", " ".join(theme["attribution"]))
        self.assertIn("入选口径：概念强度分 Top8", theme["attribution"])

    def test_theme_payload_without_members_degrades(self):
        theme = strength.theme_payload(self._cluster(), {}, zt_codes=None, benchmark=None)
        self.assertEqual(theme["n_members"], 0)
        self.assertIsNone(theme["excess5"])
        self.assertEqual(theme["leaders"], [])


class TestCandidates(unittest.TestCase):
    def test_build_candidates_filters_universe_and_merges_sources(self):
        member_rows = {
            "BK0480": [
                _stock("300581", "晨曦航空", f109=19.53, f160=21.0),
                _stock("920006", "北交所样本", f109=31.27),
                _stock("600001", "ST样本", f109=12.0),
                _stock("300777", "N新股", f109=40.0),
                _stock("600888", "零价样本", f2=0.0, f109=9.0),
                _stock("600999", "无涨幅样本", f109=None, f160=None),
            ]
        }
        market_rows = [
            _stock("300581", "晨曦航空", f109=19.53, f160=21.0),
            _stock("600123", "独立走强股", f109=12.0, f160=15.0),
        ]
        candidates = strength.build_candidates(
            member_rows, market_rows, membership={"300581": ["航天航空"]}, limit=10
        )
        codes = [item["code"] for item in candidates]
        for excluded in ("920006", "600001", "300777", "600888", "600999"):
            self.assertNotIn(excluded, codes)
        self.assertEqual(sorted(codes), ["300581", "600123"])
        by_code = {item["code"]: item for item in candidates}
        self.assertEqual(by_code["300581"]["sources"], ["theme:BK0480", "market"])
        self.assertEqual(by_code["300581"]["themes"], ["航天航空"])
        self.assertEqual(by_code["600123"]["sources"], ["market"])

    def test_blocked_concept_keywords_and_risk_names(self):
        self.assertTrue(strength._blocked_concept_name("红利股"))
        self.assertTrue(strength._blocked_concept_name("红利低波"))
        self.assertFalse(strength._blocked_concept_name("高送转"))
        self.assertFalse(strength._blocked_concept_name(""))
        # 风险股：ST/*ST/SST 与退市整理
        for name in ("ST样本", "*ST样本", "SST样本", "退市样本", "海润退"):
            self.assertTrue(strength._is_st(name))
        self.assertFalse(strength._is_st("海螺水泥"))

    def test_build_candidates_respects_limit(self):
        market_rows = [_stock(f"600{i:03d}", f"样本{i}", f109=10.0 + i) for i in range(10)]
        candidates = strength.build_candidates({}, market_rows, membership={}, limit=3)
        self.assertEqual(len(candidates), 3)

    def test_rank_stocks_score_degrades_without_tech(self):
        candidates = [
            {
                "code": "600001", "name": "一号", "price": 10.0, "change_pct": 3.0, "ret3": 5.0,
                "ret5": 20.0, "ret10": 25.0, "ret60": 30.0, "vol_ratio": 2.0, "turnover": 5.0,
                "amount": 1e8, "sources": [],
            },
            {
                "code": "600002", "name": "二号", "price": 10.0, "change_pct": 1.0, "ret3": 2.0,
                "ret5": 6.0, "ret10": 7.0, "ret60": 8.0, "vol_ratio": 1.0, "turnover": 4.0,
                "amount": 8e7, "sources": [],
            },
            {
                "code": "600003", "name": "三号", "price": 10.0, "change_pct": -1.0, "ret3": -2.0,
                "ret5": -3.0, "ret10": -5.0, "ret60": -8.0, "vol_ratio": 0.6, "turnover": 2.0,
                "amount": 3e7, "sources": [],
            },
        ]
        ranked = strength.rank_stocks(candidates, benchmark=dict(_BENCHMARK))
        self.assertEqual([item["code"] for item in ranked], ["600001", "600002", "600003"])
        self.assertGreater(ranked[0]["score"], ranked[-1]["score"])
        self.assertIsNone(ranked[0]["tech"])
        self.assertEqual(ranked[0]["role"], strength.ROLE_SOLO)
        self.assertTrue(any("缺口" in note for note in ranked[0]["tech_notes"]))
        self.assertAlmostEqual(ranked[0]["excess5"], 23.4, places=2)
        self.assertEqual(ranked[0]["stage"], strength.STAGE_LEAD)
        self.assertEqual(sorted(ranked[0]["attribution"].keys()), ["stock", "theme"])
        # 空间：主板 3 日偏离 7.5pp < 14（20 的 70%）→ 充足；未给 regime → 不追高
        self.assertEqual(ranked[0]["space"]["limit_pct"], 10.0)
        self.assertEqual(ranked[0]["space"]["label"], strength.SPACE_ROOMY)
        self.assertTrue(any("异动阈值" in note for note in ranked[0]["space"]["notes"]))
        self.assertEqual(ranked[0]["action"], strength.ACTION_DIP)
        self.assertEqual(ranked[0]["action_notes"], [])  # 默认低吸不追加说明

    def test_rank_stocks_chases_only_in_broad_regime_with_room(self):
        candidate = {
            "code": "600001", "name": "一号", "price": 10.0, "change_pct": 3.0, "ret3": 5.0,
            "ret5": 20.0, "ret10": 25.0, "ret60": 30.0, "vol_ratio": 2.0, "turnover": 5.0,
            "amount": 1e8, "sources": [],
        }
        chosen = strength.rank_stocks(
            [candidate], benchmark=dict(_BENCHMARK), regime=strength.REGIME_BROAD
        )[0]
        self.assertEqual(chosen["action"], strength.ACTION_CHASE)
        # 同票在抱团环境（大环境不允许追高）→ 低吸
        huddle = strength.rank_stocks(
            [dict(candidate)], benchmark=dict(_BENCHMARK), regime=strength.REGIME_HUDDLE
        )[0]
        self.assertEqual(huddle["action"], strength.ACTION_DIP)

    def test_rank_stocks_space_tight_means_no_b_point(self):
        tight = {
            "code": "300001", "name": "空间样本", "price": 10.0, "change_pct": 20.0, "ret3": 35.0,
            "ret5": 60.0, "ret10": 80.0, "ret60": 90.0, "vol_ratio": 2.0, "turnover": 20.0,
            "amount": 1e8, "sources": [],
        }
        ranked = strength.rank_stocks(
            [tight], benchmark=dict(_BENCHMARK), regime=strength.REGIME_BROAD
        )[0]
        self.assertEqual(ranked["space"]["label"], strength.SPACE_TIGHT)
        self.assertEqual(ranked["space"]["limit_pct"], 20.0)
        self.assertEqual(ranked["action"], strength.ACTION_SKIP)
        self.assertTrue(any("无 b 点" in note for note in ranked["action_notes"]))

    def test_is_bottom_start_requires_low_base_and_volume(self):
        ok = {
            "change_pct": 3.0, "ret3": 5.0, "ret5": 2.0, "ret10": 4.0, "ret60": 6.0,
            "vol_ratio": 2.0, "amount": 2e8, "turnover": 5.0,
        }
        self.assertTrue(strength._is_bottom_start(ok))
        self.assertFalse(strength._is_bottom_start({**ok, "ret5": 12.0}))  # 5 日已走强
        self.assertFalse(strength._is_bottom_start({**ok, "ret60": 40.0}))  # 位置不低
        self.assertFalse(strength._is_bottom_start({**ok, "vol_ratio": 1.0}))  # 缩量
        self.assertFalse(strength._is_bottom_start({**ok, "amount": 1e7}))  # 流动性不足
        self.assertFalse(strength._is_bottom_start({**ok, "turnover": 45.0}))  # 换手过热
        self.assertFalse(strength._is_bottom_start({**ok, "change_pct": -1.0}))  # 当日未转强
        self.assertFalse(strength._is_bottom_start({**ok, "ret3": -0.5}))  # 3 日未转强

    def test_rank_bottom_scores_and_shares_stock_shape(self):
        def row(code, name, change, ret3, ret5, ret60, vol_ratio, turnover):
            return {
                "code": code, "name": name, "price": 10.0, "change_pct": change,
                "ret3": ret3, "ret5": ret5, "ret10": ret5, "ret60": ret60,
                "vol_ratio": vol_ratio, "turnover": turnover, "amount": 2e8, "sources": ["bottom"],
            }

        rows = [
            row("601111", "甲", 6.0, 8.0, 3.0, 8.0, 2.5, 6.0),
            row("601112", "乙", 3.0, 5.0, 1.5, 4.0, 1.8, 4.0),
            row("601113", "丙", 1.5, 3.0, 0.5, 2.0, 1.6, 2.5),
            row("601114", "已走强", 6.0, 8.0, 20.0, 30.0, 2.5, 8.0),
        ]
        ranked = strength.rank_bottom(rows, benchmark=dict(_BENCHMARK))
        self.assertEqual([item["code"] for item in ranked], ["601111", "601112", "601113"])
        self.assertGreater(ranked[0]["score"], ranked[-1]["score"])
        self.assertEqual(ranked[0]["launch"]["points"], 1.0)  # 无日线：只有「大阳」一条
        self.assertEqual(len(ranked[0]["launch"]["miss"]), 10)
        for key in (
            "score", "stage", "space", "pullback", "action", "action_notes",
            "themes", "role", "tech", "tech_notes", "attribution", "vol_stance",
        ):
            self.assertIn(key, ranked[0])
        # 刚启动 → 3 日偏离小 → 空间充足；无回落
        self.assertEqual(ranked[0]["space"]["label"], strength.SPACE_ROOMY)
        self.assertIsNone(ranked[0]["pullback"])
        self.assertEqual(len(strength.rank_bottom(rows, limit=2)), 2)

    def test_launch_flags_weight_volume_and_shape(self):
        full = strength._launch_of(
            {"change_pct": 9.0, "main_inflow": 1.2e8, "main_inflow_pct": 6.5},
            {
                "rv20": 3.0, "rel_vol": 2.0, "mfi": 70.0,
                "vs_ma20_pct": 3.0, "vs_ma60_pct": -1.0, "close_loc": 0.9,
                "macd_hist": 0.2, "plus_di": 30.0, "minus_di": 10.0,
                "ma5": 100.0, "ma10": 101.0, "ma20": 102.0,
            },
        )
        # 量能 3 + 资金 1 + 形态 5 各 2 分，价格 1 + 方向 1 = 20（满分）
        self.assertEqual(full["points"], 20.0)
        self.assertEqual(len(full["hits"]), 11)
        self.assertEqual(full["miss"], [])
        # 无日线 → 只剩价格分，其余写成缺口
        no_tech = strength._launch_of({"change_pct": 9.0}, None)
        self.assertEqual(no_tech["points"], 1.0)
        self.assertEqual(len(no_tech["miss"]), 10)
        # 形态走坏（已远离 MA20、跌破 MA60、收在低位、MACD 柱为负）→ 量能救不回来
        broken = strength._launch_of(
            {"change_pct": 1.0},
            {
                "rv20": 3.0, "rel_vol": 2.0, "mfi": 70.0, "vs_ma20_pct": 25.0,
                "vs_ma60_pct": -12.0, "close_loc": 0.2, "macd_hist": -0.3,
                "plus_di": 10.0, "minus_di": 30.0,
            },
        )
        self.assertEqual(broken["points"], 6.0)
        # 资金单独一条：主力净流入为正但占比不足 → 未命中
        self.assertEqual(
            strength._launch_of({"change_pct": 0.0, "main_inflow": 1e8, "main_inflow_pct": 1.0}, None)[
                "points"
            ],
            0.0,
        )

    def test_rank_bottom_sorts_by_launch_flags_first(self):
        def row(code, change, ret3, ret5, vol_ratio):
            return {
                "code": code, "name": code, "price": 10.0, "change_pct": change,
                "ret3": ret3, "ret5": ret5, "ret10": ret5, "ret60": 3.0,
                "vol_ratio": vol_ratio, "turnover": 3.0, "amount": 2e8, "sources": ["bottom"],
            }

        tech_ok = {
            "rv20": 3.0, "rel_vol": 2.0, "mfi": 70.0, "vs_ma20_pct": 3.0,
            "vs_ma60_pct": -1.0, "close_loc": 0.9, "macd_hist": 0.2,
            "plus_di": 30.0, "minus_di": 10.0, "ma5": 100.0, "ma10": 101.0, "ma20": 102.0,
        }
        # 甲量价弱但标志全中；乙量价强但无日线 → 标志分优先
        rows = [row("601121", 2.0, 3.0, 1.0, 1.6), row("601122", 9.9, 9.0, 7.0, 3.0)]
        ranked = strength.rank_bottom(
            rows, tech_by_code={"601121": tech_ok}, benchmark=dict(_BENCHMARK)
        )
        self.assertEqual([item["code"] for item in ranked], ["601121", "601122"])
        # 甲：量能 3 + 形态 5 + 方向 1 = 17（当日仅 +2%，大阳未达；无主力资金字段）
        self.assertEqual(ranked[0]["launch"]["points"], 17.0)
        self.assertLess(ranked[1]["launch"]["points"], ranked[0]["launch"]["points"])

    def test_rank_stocks_missing_benchmark_uses_zero_excess(self):
        candidates = [
            {
                "code": "600001", "name": "一号", "price": 10.0, "change_pct": 1.0, "ret3": 1.0,
                "ret5": 1.0, "ret10": 1.0, "ret60": 1.0, "vol_ratio": 1.0, "turnover": 1.0,
                "amount": 1e7, "sources": [],
            }
        ]
        ranked = strength.rank_stocks(candidates, tech_by_code={"600001": _tech()})
        # 基准缺口时超额按 0 处理（analyze 会在 notes 里声明），不做「假装有基准」
        self.assertEqual(ranked[0]["excess5"], 0.0)
        self.assertEqual(ranked[0]["stage"], strength.STAGE_FLAT)
        self.assertIsNotNone(ranked[0]["tech"])
        self.assertEqual(ranked[0]["role"], strength.ROLE_SOLO)


class TestJudgmentLayer(unittest.TestCase):
    """判断层：退潮迹象（S 风险）/ 剩余空间（还有没有 b 点）/ 低吸-追高 的规则边界。"""

    def test_board_class_by_code(self):
        for code in ("300001", "301001", "302001", "688001"):
            self.assertTrue(strength._is_gem(code))
            self.assertEqual(strength._space_of(code)["limit_pct"], 20.0)
        for code in ("600001", "000001", "002001", "605001"):
            self.assertFalse(strength._is_gem(code))
            self.assertEqual(strength._space_of(code)["limit_pct"], 10.0)

    def test_space_labels_follow_abnormal_thresholds(self):
        # 主板：3 日偏离 20 达异动、14 起偏紧
        self.assertEqual(
            strength._space_of("600001", excess3=20.0)["label"], strength.SPACE_TIGHT
        )
        self.assertEqual(
            strength._space_of("600001", excess3=14.0)["label"], strength.SPACE_NARROW
        )
        self.assertEqual(
            strength._space_of("600001", excess3=13.9)["label"], strength.SPACE_ROOMY
        )
        # 创业板/科创板：阈值 30、21 起偏紧；板块标签必须跟着代码走
        gem_narrow = strength._space_of("300001", excess3=25.0)
        self.assertEqual(gem_narrow["label"], strength.SPACE_NARROW)
        self.assertEqual(gem_narrow["limit_pct"], 20.0)
        self.assertIn("创业板/科创板异动阈值 ≈30%", gem_narrow["notes"][0])
        self.assertEqual(
            strength._space_of("300001", excess3=30.0)["label"], strength.SPACE_TIGHT
        )
        # 连板高度 ≥5 板偏紧；10 日偏离 ≥100 受限
        self.assertEqual(
            strength._space_of("600001", streak=5)["label"], strength.SPACE_NARROW
        )
        self.assertEqual(
            strength._space_of("600001", streak=4)["label"], strength.SPACE_ROOMY
        )
        self.assertEqual(
            strength._space_of("600001", excess10=100.0)["label"], strength.SPACE_TIGHT
        )

    def test_space_from_zt_streak_in_rank_stocks(self):
        candidate = {
            "code": "600001", "name": "连板样本", "price": 10.0, "change_pct": 9.9,
            "ret3": 5.0, "ret5": 20.0, "ret10": 25.0, "ret60": 30.0, "vol_ratio": 2.0,
            "turnover": 5.0, "amount": 1e8, "sources": [],
        }
        ranked = strength.rank_stocks(
            [candidate], benchmark=dict(_BENCHMARK), zt_streak={"600001": 6}
        )[0]
        self.assertEqual(ranked["space"]["streak"], 6)
        self.assertEqual(ranked["space"]["label"], strength.SPACE_NARROW)
        self.assertEqual(ranked["action"], strength.ACTION_DIP)

    def test_action_rules_by_regime_and_stage(self):
        roomy = strength._space_of("600001", excess3=1.0)
        tight = strength._space_of("600001", excess3=25.0)
        # 大环境：普跌无主线 → 不参与（追高与低吸都被环境压制）
        action, why = strength._action_for(strength.STAGE_LEAD, roomy, strength.REGIME_DOWN)
        self.assertEqual(action, strength.ACTION_SKIP)
        self.assertTrue(any("普跌" in line for line in why))
        # 个股退潮 → 不参与
        self.assertEqual(
            strength._action_for(strength.STAGE_EBB, roomy, strength.REGIME_HUDDLE)[0],
            strength.ACTION_SKIP,
        )
        # 空间受限（强势但没 b 点）→ 不参与
        action, why = strength._action_for(strength.STAGE_LEAD, tight, strength.REGIME_BROAD)
        self.assertEqual(action, strength.ACTION_SKIP)
        self.assertTrue(any("无 b 点" in line for line in why))
        # 扩散 + 领涨 + 空间充足 → 追高；同条件下抱团 → 低吸（默认不追加说明）
        self.assertEqual(
            strength._action_for(strength.STAGE_LEAD, roomy, strength.REGIME_BROAD)[0],
            strength.ACTION_CHASE,
        )
        # 所在线已现退潮（s_risk 高）→ 即使环境扩散也不追高
        action, why = strength._action_for(
            strength.STAGE_LEAD, roomy, strength.REGIME_BROAD, "高"
        )
        self.assertEqual(action, strength.ACTION_DIP)
        self.assertTrue(any("s_risk 高" in line for line in why))
        action, why = strength._action_for(strength.STAGE_LEAD, roomy, strength.REGIME_HUDDLE)
        self.assertEqual(action, strength.ACTION_DIP)
        self.assertEqual(why, [])
        # 技术面走坏（近跌停/放量下跌/跌破 MA5）→ 不参与，不能当低吸机会
        broken_tech = {"rel_vol": 2.85, "vs_ma5_pct": 2.66, "close_loc": 0.0}
        action, why = strength._action_for(
            strength.STAGE_LEAD,
            roomy,
            strength.REGIME_HUDDLE,
            None,
            strength._pullback_of(-9.97, -5.0, broken_tech),
        )
        self.assertEqual(action, strength.ACTION_SKIP)
        self.assertTrue(any("技术面走坏" in line for line in why))
        # 高位回落 → 低吸，并提示昨日可能已是 S 点
        action, why = strength._action_for(strength.STAGE_COOL, roomy, strength.REGIME_HUDDLE)
        self.assertEqual(action, strength.ACTION_DIP)
        self.assertTrue(any("S 点" in line for line in why))
        # regime 缺口 → 按「结构不明」处理，不追高、不追加说明
        action, why = strength._action_for(strength.STAGE_LEAD, roomy, None)
        self.assertEqual((action, why), (strength.ACTION_DIP, []))

    def test_pullback_states_are_technical_only(self):
        # 未回落 → 不给该字段
        self.assertIsNone(strength._pullback_of(2.0, 5.0, _tech()))
        # 缩量回踩：量能收、仍在 MA5 上方
        shrink = strength._pullback_of(-2.0, -1.0, {"rel_vol": 0.7, "vs_ma5_pct": 3.0})
        self.assertEqual(shrink["state"], strength.PULLBACK_SHRINK)
        self.assertTrue(any("缩量回踩" in line for line in shrink["notes"]))
        # 放量下跌 → 走坏
        self.assertEqual(
            strength._pullback_of(-5.0, -3.0, {"rel_vol": 1.5, "vs_ma5_pct": 2.0})["state"],
            strength.PULLBACK_BROKEN,
        )
        # 跌破 MA5 → 走坏
        self.assertEqual(
            strength._pullback_of(-1.0, -0.5, {"rel_vol": 0.8, "vs_ma5_pct": -2.0})["state"],
            strength.PULLBACK_BROKEN,
        )
        # 近跌停 → 走坏（即使没有日线）
        self.assertEqual(
            strength._pullback_of(-9.97, -5.0, None)["state"], strength.PULLBACK_BROKEN
        )
        # 技术面缺口：不硬判缩量
        gap = strength._pullback_of(-3.0, -2.0, None)
        self.assertEqual(gap["state"], strength.PULLBACK_MIXED)
        self.assertTrue(any("缺口" in line for line in gap["notes"]))

    def test_theme_fade_signals_and_risk(self):
        signals, level = strength._theme_fade(
            {"stage": strength.STAGE_COOL, "change_pct": -3.2, "vol_ratio": 1.4},
            {"strong_member_up_ratio": 0.25, "n_members": 30},
        )
        self.assertEqual(level, "高")  # 阶段回落 + 承接转弱
        self.assertTrue(any("高位回落" in line for line in signals))
        self.assertTrue(any("承接转弱" in line for line in signals))
        signals, level = strength._theme_fade(
            {"stage": strength.STAGE_LEAD, "change_pct": 4.0, "vol_ratio": 1.2},
            {"strong_member_up_ratio": 0.7, "n_members": 20},
        )
        self.assertEqual((signals, level), ([], "低"))
        # 成员太少（3 只）时 1/3 上涨不是承接转弱信号
        signals, level = strength._theme_fade(
            {"stage": strength.STAGE_LEAD, "change_pct": 1.0, "vol_ratio": 1.1},
            {"strong_member_up_ratio": 0.33, "n_members": 3},
        )
        self.assertEqual((signals, level), ([], "低"))


class TestAnalyze(unittest.TestCase):
    def _payload(self):
        concepts = [
            _board("BK1157", "飞行汽车(eVTOL)", f3=4.17, f127=-1.88, f109=-3.11, f160=-7.70, f24=-4.45,
                   f104=77, f105=11, f10=1.33, f128="万丰奥威", f136=9.99),
            _board("BK0480", "航天航空", f3=4.51, f127=0.48, f109=0.77, f160=-3.91, f24=0.04,
                   f104=54, f105=4, f10=1.66, f128="晨曦航空", f136=20.02),
            _board("BK0733", "低空经济", f3=3.12, f127=-0.42, f109=-2.20, f160=-5.10, f24=1.10,
                   f104=61, f105=18, f10=1.21, f128="中信海直", f136=10.01),
            _board("BK0001", "昨日连板_含一字", f3=9.0, f109=12.0),
            _board("BK0002", "证金持股", f3=0.5, f109=1.0),
            # 多周期还弱（进不了强度 TopN）但当日+3 日转正 → 只能走「启动分」通道
            _board("BK7777", "刚转强概念", f3=3.5, f127=2.5, f109=-7.0, f160=-9.0, f24=-8.0,
                   f104=50, f105=6, f10=1.5, f128="刚转强A"),
            _board("BK1641", "红利股", f3=0.5, f127=0.3, f109=3.0, f160=4.0, f24=6.0,
                   f104=70, f105=25, f10=1.0, f128="海螺水泥"),
            *_weak_concepts(),
        ]
        industries = [
            _board("BK1036", "汽车零部件", f3=2.5, f127=0.3, f109=1.2, f160=0.8, f24=6.0,
                   f104=120, f105=90, f10=1.2, f128="均胜电子"),
            _board("BK0481", "航空装备", f3=4.0, f127=0.5, f109=2.98, f160=1.5, f24=4.0,
                   f104=40, f105=6, f10=1.9, f128="晨曦航空"),
            _board("BK1044", "出版", f3=1.0, f127=0.5, f109=3.0, f160=4.0, f24=10.0,
                   f104=10, f105=10, f10=1.0, f128="出版样本"),
            _board("BK1045", "大众出版", f3=2.0, f127=1.0, f109=8.82, f160=13.59, f24=20.0,
                   f104=8, f105=8, f10=1.13, f128="新华传媒"),
            _board("BK1046", "单票行业", f3=1.0, f127=1.0, f109=6.5, f160=5.0, f24=12.0,
                   f104=1, f105=0, f10=1.4, f128="华利集团"),
        ]
        members = {
            "BK0480": [
                _stock("300581", "晨曦航空", f3=20.02, f127=12.0, f109=19.53, f160=21.0, f24=29.64, f10=3.2),
                _stock("300045", "华力创通", f3=20.04, f127=7.5, f109=-0.31, f160=8.92, f24=-18.49, f10=4.65),
                _stock("600038", "中直股份", f3=6.10, f127=2.0, f109=5.5, f160=4.0, f24=-3.0, f10=1.8),
            ],
            "BK0733": [
                _stock("000099", "中信海直", f3=10.01, f127=4.0, f109=7.0, f160=6.0, f24=3.0, f10=2.0),
                _stock("300045", "华力创通", f3=20.04, f127=7.5, f109=-0.31, f160=8.92, f24=-18.49, f10=4.65),
                _stock("002085", "万丰奥威", f3=9.99, f127=3.0, f109=6.5, f160=5.0, f24=2.0, f10=2.4),
            ],
            "BK1157": [
                _stock("002085", "万丰奥威", f3=9.99, f127=3.0, f109=6.5, f160=5.0, f24=2.0, f10=2.4),
                _stock("300681", "英搏尔", f3=20.0, f127=9.0, f109=14.0, f160=12.0, f24=1.0, f10=3.9),
                _stock("300045", "华力创通", f3=20.04, f127=7.5, f109=-0.31, f160=8.92, f24=-18.49, f10=4.65),
            ],
            "BK1045": [
                _stock("600825", "新华传媒", f3=10.01, f127=10.01, f109=20.99, f160=95.11, f24=207.51, f10=0.68),
                _stock("301052", "果麦文化", f3=3.0, f127=22.02, f109=42.97, f160=49.32, f24=58.58, f10=1.28),
                _stock("601811", "新华文轩", f3=10.01, f127=33.11, f109=34.58, f160=38.28, f24=126.94, f10=1.0),
            ],
            "BK1044": [
                _stock("601811", "新华文轩", f3=10.01, f127=33.11, f109=34.58, f160=38.28, f24=126.94, f10=1.0),
                _stock("600825", "新华传媒", f3=10.01, f127=10.01, f109=20.99, f160=95.11, f24=207.51, f10=0.68),
                # 屏蔽名单成员混进非屏蔽板块：必须在源头剔除（不进领涨股/个股榜）
                _stock("600019", "宝钢股份", f3=10.0, f127=40.0, f109=50.0, f160=60.0, f24=70.0, f10=3.0, f8=5.0),
            ],
            "BK1046": [
                _stock("300979", "华利集团", f3=3.0, f127=2.0, f109=6.5, f160=5.0, f24=12.0, f10=1.4),
            ],
            "BK0481": [
                _stock("300581", "晨曦航空", f3=20.02, f127=12.0, f109=19.53, f160=21.0, f24=29.64, f10=3.2),
                _stock("600038", "中直股份", f3=6.10, f127=2.0, f109=5.5, f160=4.0, f24=-3.0, f10=1.8),
            ],
            "BK1036": [
                _stock("002085", "万丰奥威", f3=9.99, f127=3.0, f109=6.5, f160=5.0, f24=2.0, f10=2.4),
            ],
            # 刚转强概念：成员独立（与其它线无重叠），保证不被 Jaccard 合并
            "BK7777": [
                _stock("603001", "刚转强A", f3=3.0, f127=4.0, f109=2.0, f160=1.0, f24=0.5, f10=1.6),
                _stock("603002", "刚转强B", f3=2.0, f127=3.0, f109=1.0, f160=0.5, f24=0.2, f10=1.4),
                _stock("603003", "刚转强C", f3=4.0, f127=5.0, f109=1.5, f160=0.8, f24=0.3, f10=1.8),
            ],
            # 红利类概念：命中屏蔽名单（成员本可进底部启动榜，须被剔除）
            "BK1641": [
                _stock("600585", "海螺水泥", f3=6.45, f127=8.18, f109=7.19, f160=10.95,
                       f24=7.0, f10=4.09, f6=1.5e9, f8=2.16),
                _stock("601857", "中国石油", f3=1.35, f127=4.16, f109=10.0, f160=16.53,
                       f24=31.14, f10=0.83, f8=1.0),
                _stock("600019", "宝钢股份", f3=1.17, f127=2.72, f109=2.9, f160=3.42,
                       f24=8.83, f10=0.96, f8=1.0),
            ],
        }
        market_top = [
            _stock("600123", "独立走强股", f3=5.0, f127=3.0, f109=12.0, f160=15.0, f24=20.0, f10=2.2),
            _stock("920006", "北交所样本", f3=29.98, f109=31.27),
            _stock("600999", "无涨幅样本", f109=None, f160=None),
        ]
        zt = [
            {"代码": "300581", "名称": "晨曦航空", "所属行业": "航空装备", "连板数": 1},
            {"代码": "300681", "名称": "英搏尔", "所属行业": "汽车零部件", "连板数": 2},
            {"代码": "600825", "名称": "新华传媒", "所属行业": "出版", "连板数": 1},
        ]
        style = {
            "ok": True,
            "as_of": _DAY,
            "state": "红利防守",
            "reasons": ["红利超额5日+3.40%"],
            "benchmark": dict(_BENCHMARK),
        }
        bottom = [
            _stock("601111", "底部启动A", f3=6.0, f127=8.0, f109=3.0, f160=5.0, f24=8.0,
                   f10=2.5, f6=2e8, f8=6.0, f62=1.2e8, f184=5.5),
            _stock("601112", "底部启动B", f3=3.0, f127=5.0, f109=1.5, f160=2.0, f24=4.0,
                   f10=1.8, f6=1.5e8, f8=4.0),
            _stock("601113", "底部启动C", f3=1.5, f127=3.0, f109=0.5, f160=1.0, f24=2.0,
                   f10=1.6, f6=1.2e8, f8=2.5),
            _stock("601114", "已走强不入选", f3=6.0, f127=8.0, f109=20.0, f160=25.0,
                   f24=30.0, f10=2.5, f6=2e8, f8=8.0),
            _stock("601115", "缩量不入选", f3=6.0, f127=8.0, f109=3.0, f160=5.0, f24=8.0,
                   f10=0.8, f6=2e8, f8=6.0),
        ]
        tech_codes = [
            "300581", "300045", "600038", "000099", "002085", "300681",
            "600825", "301052", "601811", "300979", "600123",
            "601111", "601112", "601113",
        ]
        tech = {code: _tech(rel_vol=2.0) for code in tech_codes}
        return strength.analyze(
            top_concepts=4,
            top_industries=2,
            max_stocks=4,
            max_candidates=9,
            board_rows={"concept": concepts, "industry": industries},
            members_by_board=members,
            market_top_rows=market_top,
            zt_rows=zt,
            style_payload=style,
            tech_by_code=tech,
            bottom_rows=bottom,
        )

    def test_analyze_pipeline_offline(self):
        payload = self._payload()
        self.assertEqual(payload["quote_trade_date"], _DAY)
        self.assertEqual(payload["regime"]["regime"], strength.REGIME_HUDDLE)
        self.assertEqual(payload["meta"]["n_concepts_excluded"], 2)  # 昨日连板 / 证金持股
        self.assertEqual(payload["errors"], [])

        names = [theme["name"] for theme in payload["themes"]]
        self.assertIn("航天航空", names)
        self.assertIn("大众出版", names)
        # 单票行业（成员<3）不成主题线，但仍在 boards.industry 里
        self.assertNotIn("单票行业", names)
        self.assertIn("单票行业", [b["name"] for b in payload["boards"]["industry"]])
        self.assertTrue(any("单票行业" in note for note in payload["notes"]))

        # 低空经济与飞行汽车成分重叠 → 合并成一条线（主名取分位高的）
        merged = next((t for t in payload["themes"] if t["aliases"]), None)
        self.assertIsNotNone(merged)
        self.assertIn("低空经济", [merged["name"], *merged["aliases"]])

        # 「刚转强」通道：多周期弱但当日+3日转正 → 由启动分入选（不挤占强度分名额）
        turn_theme = next(t for t in payload["themes"] if t["name"] == "刚转强概念")
        self.assertIn("启动分", turn_theme["picked_by"])
        self.assertIsNotNone(turn_theme["turn_score"])
        self.assertNotIn(
            "刚转强概念", [b["name"] for b in payload["boards"]["concept"]]
        )

        hangkong = next(theme for theme in payload["themes"] if theme["name"] == "航天航空")
        self.assertGreaterEqual(hangkong["n_members"], 3)
        # 航天航空/低空经济/飞行汽车 成分重叠 → 已并成一条线，涨停家数是全线的
        self.assertGreaterEqual(hangkong["limit_up_count"], 1)
        self.assertTrue(hangkong["attribution"])
        # 退潮迹象（S 风险）：字段恒在，有迹象时必带数字
        for theme in payload["themes"]:
            self.assertIn(theme["s_risk"], ("低", "中", "高"))
            self.assertIsInstance(theme["fade_signals"], list)

        codes = [stock["code"] for stock in payload["stocks"]]
        self.assertTrue(codes)
        for excluded in ("920006", "600999"):
            self.assertNotIn(excluded, codes)
        top = payload["stocks"][0]
        self.assertEqual(sorted(top["attribution"].keys()), ["stock", "theme"])
        self.assertTrue(payload["market"]["context"])
        # 市场因素只给一次（顶层），不在每只票里重复
        self.assertNotIn("market", top["attribution"])
        for stock in payload["stocks"]:
            self.assertIn(stock["space"]["label"], (strength.SPACE_ROOMY, strength.SPACE_NARROW, strength.SPACE_TIGHT))
            self.assertIn(stock["action"], (strength.ACTION_CHASE, strength.ACTION_DIP, strength.ACTION_SKIP))
            self.assertTrue(stock["pullback"] is None or stock["pullback"]["state"] in (
                strength.PULLBACK_SHRINK, strength.PULLBACK_MIXED, strength.PULLBACK_BROKEN
            ))
        # 屏蔽名单：红利类概念不进主题线/个股榜，但 boards 保留原始截面
        self.assertIn("红利股", [b["name"] for b in payload["boards"]["concept"]])
        self.assertNotIn("红利股", [t["name"] for t in payload["themes"]])
        for blocked in ("600585", "601857", "600019"):
            self.assertNotIn(blocked, [s["code"] for s in payload["stocks"]])
            self.assertNotIn(blocked, [s["code"] for s in payload["bottom"]])
        self.assertTrue(any("已屏蔽红利类概念" in note for note in payload["notes"]))
        # 屏蔽票即使混进非屏蔽板块，也不出现在领涨股里
        leaders = [leader["code"] for t in payload["themes"] for leader in t["leaders"]]
        self.assertNotIn("600019", leaders)
        self.assertTrue(payload["boards"]["concept"])
        self.assertNotIn("turn", payload["boards"])
        self.assertTrue(payload["boards"]["weak_concept"])
        # 底部启动：与强势榜同形状；已走强/缩量的不进榜
        bottom = payload["bottom"]
        self.assertEqual([s["code"] for s in bottom], ["601111", "601112", "601113"])
        self.assertEqual(sorted(bottom[0].keys()), sorted(top.keys()))
        self.assertGreater(bottom[0]["launch"]["points"], bottom[-1]["launch"]["points"])
        self.assertTrue(bottom[0]["launch"]["hits"])
        self.assertEqual(bottom[0]["main_inflow"], 1.2e8)
        self.assertEqual(bottom[0]["main_inflow_pct"], 5.5)
        self.assertIsNone(top["launch"])  # 强势榜不带启动标志
        self.assertEqual(bottom[0]["space"]["label"], strength.SPACE_ROOMY)
        self.assertIsNone(bottom[0]["pullback"])
        self.assertEqual(payload["market"]["benchmark"]["ret5_hs300"], -3.4)
        self.assertEqual(payload["as_of_dates"], [_DAY])
        # meta 只留计数，不回显入参
        self.assertNotIn("max_stocks", payload["meta"])
        self.assertNotIn("turn_extra", payload["meta"])

    def test_analyze_raises_without_any_data(self):
        with self.assertRaises(ValueError):
            strength.analyze(
                style_payload={"ok": False, "state": None, "benchmark": {}},
                board_rows={"concept": [], "industry": []},
                members_by_board={},
                market_top_rows=[],
                zt_rows=[],
                bottom_rows=[],
            )

    def test_analyze_validates_arguments(self):
        with self.assertRaises(ValueError):
            strength.analyze(top_concepts=0)
        with self.assertRaises(ValueError):
            strength.analyze(max_candidates=999)
        with self.assertRaises(ValueError):
            strength.analyze(max_stocks=True)
        with self.assertRaises(ValueError):
            strength.analyze(top_industries=-1)


class TestToolWiring(unittest.TestCase):
    def test_tool_strength_registered_with_schema_and_cache(self):
        from stocks.ai import tools as ai_tools

        names = {item["function"]["name"] for item in ai_tools.schemas()}
        self.assertIn("strength", names)
        self.assertIn("strength", ai_tools._CACHEABLE_TOOLS)
        self.assertIn("strength", ai_tools._HANDLERS)

    def test_tool_strength_passes_args_and_validates(self):
        from stocks.ai import tools as ai_tools

        with mock.patch("stocks.strength.analyze", return_value={"ok_marker": True}) as analyze:
            result = ai_tools.call(
                "strength",
                {"top_concepts": 4, "top_industries": 3, "max_stocks": 5},
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"], {"ok_marker": True})
        kwargs = analyze.call_args.kwargs
        self.assertEqual(kwargs["top_concepts"], 4)
        self.assertEqual(kwargs["top_industries"], 3)
        self.assertEqual(kwargs["max_stocks"], 5)
        self.assertNotIn("turn_extra", kwargs)
        self.assertNotIn("top_concepts", analyze.call_args.args)

        bad = ai_tools.call("strength", {"max_stocks": 999})
        self.assertFalse(bad["ok"])
        self.assertIn("max_stocks", bad["error"])

    def test_tool_strength_reports_failure(self):
        from stocks.ai import tools as ai_tools

        with mock.patch("stocks.strength.analyze", return_value={}):
            result = ai_tools.call("strength", {})
        self.assertFalse(result["ok"])
        self.assertIn("近期强势", result["error"])


if __name__ == "__main__":
    unittest.main()
