import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main_controller
from stocks.ai import chat as ai_chat
from stocks.ai import search as ai_search
from stocks.ai import tools as ai_tools
from stocks.ai.llm import ConfigError, client, settings


class TestLlmConfig(unittest.TestCase):
    def test_settings_requires_env(self):
        with patch.dict(os.environ, {"OMG_DISABLE_DOTENV": "1"}, clear=True):
            with self.assertRaises(ConfigError):
                settings()

    def test_settings_ok(self):
        env = {
            "OMG_DISABLE_DOTENV": "1",
            "OMG_LLM_BASE_URL": "https://example.com/v1",
            "OMG_LLM_API_KEY": "sk-test",
            "OMG_LLM_MODEL": "demo",
            "OMG_LLM_TIMEOUT": "30",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = settings()
        self.assertEqual(cfg["model"], "demo")
        self.assertEqual(cfg["timeout"], 30)

    def test_settings_rejects_excessive_timeout(self):
        env = {
            "OMG_DISABLE_DOTENV": "1",
            "OMG_LLM_BASE_URL": "https://example.com/v1",
            "OMG_LLM_API_KEY": "sk-test",
            "OMG_LLM_MODEL": "demo",
            "OMG_LLM_TIMEOUT": "999999",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                settings()

    def test_settings_rejects_invalid_timeout(self):
        env = {
            "OMG_DISABLE_DOTENV": "1",
            "OMG_LLM_BASE_URL": "https://example.com/v1",
            "OMG_LLM_API_KEY": "sk-test",
            "OMG_LLM_MODEL": "demo",
            "OMG_LLM_TIMEOUT": "invalid",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                settings()

    @patch("openai.OpenAI")
    def test_client_disables_sdk_retries(self, mock_openai):
        env = {
            "OMG_DISABLE_DOTENV": "1",
            "OMG_LLM_BASE_URL": "https://example.com/v1",
            "OMG_LLM_API_KEY": "sk-test",
            "OMG_LLM_MODEL": "demo",
        }
        with patch.dict(os.environ, env, clear=True):
            client()
        self.assertEqual(mock_openai.call_args.kwargs["max_retries"], 0)


class TestTools(unittest.TestCase):
    def test_schemas_cover_expected_names(self):
        names = {s["function"]["name"] for s in ai_tools.schemas()}
        self.assertEqual(
            names,
            {"mood", "style", "rot", "crowd", "etf", "hot", "list", "rank", "web_search"},
        )

    def test_unknown_tool(self):
        result = ai_tools.call("nope", {})
        self.assertFalse(result["ok"])
        self.assertIn("未知", result["error"])

    @patch("stocks.mood.analyze", return_value=[{"state": "主升", "date": "2026-07-16"}])
    def test_mood_tool(self, _mock):
        result = ai_tools.call("mood", {"days": 5})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"][0]["state"], "主升")

    @patch(
        "stocks.rank.analyze",
        return_value={
            "as_of": "2026-07-20T15:00:00",
            "universe": "hs_main_cyb_kcb_ex_st",
            "gainers": [
                {
                    "code": "600000",
                    "name": "浦发银行",
                    "change_pct": 5.0,
                    "price": 10.0,
                    "amount": 1.0,
                    "turnover": 1.0,
                    "industry": "银行",
                    "concepts": ["银行"],
                }
            ],
            "losers": [],
            "meta": {
                "gainers_n": 50,
                "losers_n": 20,
                "scanned": 100,
                "filtered": 80,
                "tag_miss": 0,
            },
        },
    )
    def test_rank_tool(self, mock_analyze):
        result = ai_tools.call("rank", {"gainers": 10, "losers": 5})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["gainers"][0]["code"], "600000")
        mock_analyze.assert_called_once_with(gainers=10, losers=5)

    def test_rank_rejects_excessive_gainers(self):
        result = ai_tools.call("rank", {"gainers": 101})
        self.assertFalse(result["ok"])
        self.assertIn("gainers", result["error"])

    @patch(
        "stocks.rank.analyze",
        return_value={
            "as_of": "2026-07-20T15:00:00",
            "universe": "hs_main_cyb_kcb_ex_st",
            "gainers": [
                {
                    "code": "600000",
                    "name": "浦发银行",
                    "change_pct": 5.0,
                    "price": 10.0,
                    "amount": 1.0,
                    "turnover": 1.0,
                    "industry": "银行",
                    "concepts": ["银行"],
                }
            ],
            "losers": [],
            "meta": {
                "gainers_n": 50,
                "losers_n": 20,
                "scanned": 100,
                "filtered": 80,
                "tag_miss": 0,
            },
        },
    )
    def test_rank_tool(self, mock_analyze):
        result = ai_tools.call("rank", {"gainers": 10, "losers": 5})
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["gainers"][0]["code"], "600000")
        mock_analyze.assert_called_once_with(gainers=10, losers=5)

    def test_rank_rejects_excessive_gainers(self):
        result = ai_tools.call("rank", {"gainers": 101})
        self.assertFalse(result["ok"])
        self.assertIn("gainers", result["error"])

    @patch(
        "stocks.mood.analyze",
        return_value=[
            {
                "trade_date": "2026-07-16",
                "state": "主升",
                "metrics": {"limit_up": 80},
                "reasons": ["赚钱效应全面"],
                "pools": {"zt": [{"代码": "000001"}], "zb": [], "dt": [], "prev": []},
            }
        ],
    )
    def test_mood_tool_omits_raw_pool_rows(self, _mock):
        result = ai_tools.call("mood", {"days": 5})

        self.assertTrue(result["ok"])
        self.assertNotIn("pools", result["data"][0])

    @patch("stocks.mood.analyze", return_value=[{"state": "主升"}])
    def test_mood_uses_environment_default_days(self, mock_analyze):
        with patch.dict(os.environ, {"OMG_MOOD_DAYS": "12"}):
            self.assertTrue(ai_tools.call("mood", {})["ok"])
        mock_analyze.assert_called_once_with(days=12)

    @patch(
        "stocks.style.analyze",
        return_value={
            "state": "均衡扩散",
            "reasons": ["x"],
            "trend": [],
            "features": {"ok": True, "frame": object()},
        },
    )
    def test_style_strips_frame(self, _mock):
        result = ai_tools.call("style", {})
        self.assertTrue(result["ok"])
        self.assertNotIn("frame", result["data"]["features"])

    @patch(
        "stocks.rot.analyze",
        return_value={"回流优先": [], "升温接力": [], "放弃观察": []},
    )
    def test_rot_empty_groups_is_error(self, _mock):
        self.assertFalse(ai_tools.call("rot", {})["ok"])

    def test_web_search_empty_query(self):
        result = ai_tools.call("web_search", {"query": "  "})
        self.assertFalse(result["ok"])

    @patch("stocks.ana.concept_strength_top10", return_value={})
    def test_hot_empty_is_error(self, _mock):
        self.assertFalse(ai_tools.call("hot", {})["ok"])

    @patch("stocks.mood.analyze")
    def test_mood_rejects_excessive_days(self, mock_analyze):
        result = ai_tools.call("mood", {"days": 1000000})
        self.assertFalse(result["ok"])
        mock_analyze.assert_not_called()

    @patch("stocks.mood.analyze")
    def test_mood_rejects_zero_days(self, mock_analyze):
        result = ai_tools.call("mood", {"days": 0})
        self.assertFalse(result["ok"])
        mock_analyze.assert_not_called()

    @patch("stocks.mood.analyze")
    def test_mood_rejects_non_integer_days(self, mock_analyze):
        for value in (True, 1.5, "5"):
            self.assertFalse(ai_tools.call("mood", {"days": value})["ok"])
        mock_analyze.assert_not_called()

    @patch("stocks.watch_list.get_watch_list_current_price", return_value=[])
    @patch("stocks.market.get_index_current_price", return_value=None)
    def test_list_all_sources_failed_is_error(self, _index, _watch):
        result = ai_tools.call("list", {})
        self.assertFalse(result["ok"])

    @patch(
        "stocks.watch_list.get_watch_list_current_price",
        side_effect=RuntimeError("watch failed"),
    )
    @patch("stocks.market.get_index_current_price")
    def test_list_keeps_indexes_when_watchlist_fails(self, mock_index, _watch):
        mock_index.return_value = types.SimpleNamespace(
            code="000001",
            name="上证指数",
            price=3000.0,
            change_percent=1.0,
            amount=1,
        )
        result = ai_tools.call("list", {})
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["indexes"]), 3)
        self.assertTrue(result["data"]["errors"])

    @patch("stocks.watch_list.get_watch_list_current_price", return_value=[])
    @patch("stocks.market.get_index_current_price")
    def test_list_reports_partial_index_failure(self, mock_index, _watch):
        row = types.SimpleNamespace(
            code="000001",
            name="上证指数",
            price=3000.0,
            change_percent=1.0,
            amount=1,
        )
        mock_index.side_effect = [None, row, row]
        result = ai_tools.call("list", {})
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["indexes"]), 2)
        self.assertIn("无数据", result["data"]["errors"][0])

    def test_jsonable_converts_numpy_scalars_and_missing_values(self):
        import numpy as np
        import pandas as pd

        result = ai_tools._jsonable(
            {"int": np.int64(3), "float": np.float32(1.5), "nan": np.float32("nan"), "na": pd.NA}
        )
        self.assertEqual(result["int"], 3)
        self.assertIsInstance(result["int"], int)
        self.assertEqual(result["float"], 1.5)
        self.assertIsNone(result["nan"])
        self.assertIsNone(result["na"])
        self.assertIsNone(ai_tools._jsonable(pd.NaT))


class TestSearch(unittest.TestCase):
    def test_unknown_backend(self):
        with patch.dict(os.environ, {"OMG_SEARCH_BACKEND": "bing"}):
            with self.assertRaises(ValueError):
                ai_search.web_search("A股")

    def test_rejects_out_of_range_max_results(self):
        with self.assertRaises(ValueError):
            ai_search.web_search("A股", max_results=0)
        with self.assertRaises(ValueError):
            ai_search.web_search("A股", max_results=1.5)

    def test_rejects_invalid_environment_max_results(self):
        with patch.dict(os.environ, {"OMG_SEARCH_MAX_RESULTS": "invalid"}):
            with self.assertRaises(ValueError):
                ai_search.web_search("A股")

    @patch("ddgs.DDGS")
    def test_ddgs_maps_fields(self, mock_ddgs):
        inst = mock_ddgs.return_value.__enter__.return_value
        inst.text.return_value = [
            {"title": "t", "href": "http://x", "body": "b"},
        ]
        rows = ai_search.web_search("q", max_results=1)
        self.assertEqual(rows, [{"title": "t", "href": "http://x", "body": "b"}])


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message


class _Resp:
    def __init__(self, message):
        self.choices = [_Choice(message)]


class TestChatLoop(unittest.TestCase):
    def test_trim_keeps_system_and_tail(self):
        msgs = [{"role": "system", "content": "s"}] + [
            {"role": "user", "content": str(i)} for i in range(25)
        ]
        out = ai_chat.trim_messages(msgs, keep=3)
        self.assertEqual(out[0]["role"], "system")
        self.assertEqual(len(out), 4)
        self.assertEqual(out[-1]["content"], "24")

    def test_trim_drops_orphan_leading_tools(self):
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "old"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "mood", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "{}"},
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "ans"},
        ]
        # keep=3 → 窗口从 orphan tool 开始，应丢掉残缺 tool
        out = ai_chat.trim_messages(msgs, keep=3)
        roles = [m["role"] for m in out if m["role"] != "system"]
        self.assertNotIn("tool", roles)
        self.assertEqual(roles[-1], "assistant")

    @patch("stocks.ai.tools.call")
    def test_ask_once_runs_tool_then_answers(self, mock_call):
        mock_call.return_value = {"ok": True, "data": {"state": "主升"}, "error": None}
        tc = types.SimpleNamespace(
            id="call1",
            function=types.SimpleNamespace(
                name="mood", arguments=json.dumps({"days": 3})
            ),
        )
        oai = MagicMock()
        oai.chat.completions.create.side_effect = [
            _Resp(_Msg(tool_calls=[tc])),
            _Resp(_Msg(content="情绪偏主升")),
        ]
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
        text = ai_chat.ask_once(oai, "demo", messages)
        self.assertEqual(text, "情绪偏主升")
        mock_call.assert_called_once_with("mood", {"days": 3})
        self.assertEqual(messages[-1]["role"], "assistant")

    @patch("stocks.ai.tools.call")
    def test_ask_once_responds_all_tool_calls_when_over_limit(self, mock_call):
        mock_call.return_value = {"ok": True, "data": 1, "error": None}
        tcs = [
            types.SimpleNamespace(
                id=f"c{i}",
                function=types.SimpleNamespace(name="mood", arguments="{}"),
            )
            for i in range(3)
        ]
        oai = MagicMock()
        oai.chat.completions.create.side_effect = [
            _Resp(_Msg(tool_calls=tcs)),
            _Resp(_Msg(content="done")),
        ]
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
        text = ai_chat.ask_once(oai, "demo", messages, max_tool_calls=1)
        self.assertEqual(text, "done")
        self.assertEqual(mock_call.call_count, 1)
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 3)
        limited = json.loads(tool_msgs[1]["content"])
        self.assertFalse(limited["ok"])
        self.assertIn("上限", limited["error"])

    @patch("stocks.ai.tools.call")
    def test_ask_once_rejects_malformed_tool_arguments(self, mock_call):
        tc = types.SimpleNamespace(
            id="bad",
            function=types.SimpleNamespace(name="mood", arguments="{bad"),
        )
        oai = MagicMock()
        oai.chat.completions.create.side_effect = [
            _Resp(_Msg(tool_calls=[tc])),
            _Resp(_Msg(content="参数错误")),
        ]
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
        self.assertEqual(ai_chat.ask_once(oai, "demo", messages), "参数错误")
        mock_call.assert_not_called()
        result = json.loads(next(m for m in messages if m["role"] == "tool")["content"])
        self.assertFalse(result["ok"])
        self.assertIn("JSON", result["error"])

    @patch("stocks.ai.tools.call")
    def test_large_tool_batch_is_preserved_for_final_answer(self, mock_call):
        mock_call.return_value = {"ok": True, "data": 1, "error": None}
        calls = [
            types.SimpleNamespace(
                id=f"c{i}",
                function=types.SimpleNamespace(name="mood", arguments="{}"),
            )
            for i in range(19)
        ]
        oai = MagicMock()
        oai.chat.completions.create.side_effect = [
            _Resp(_Msg(tool_calls=calls)),
            _Resp(_Msg(content="done")),
        ]
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
        self.assertEqual(ai_chat.ask_once(oai, "demo", messages), "done")
        final_messages = oai.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertTrue(any(m.get("role") == "user" for m in final_messages))
        self.assertEqual(
            len([m for m in final_messages if m.get("role") == "tool"]),
            19,
        )

    @patch("stocks.ai.chat.ask_once", side_effect=RuntimeError("network"))
    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_prompt_failure_then_eof_returns_nonzero(self, _client, _ask):
        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(ai_chat.run(prompt="q"), 1)

    @patch("stocks.ai.chat.ask_once", side_effect=KeyboardInterrupt)
    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_ctrl_c_during_request_exits_cleanly(self, _client, _ask):
        self.assertEqual(ai_chat.run(prompt="q"), 0)

    @patch("stocks.ai.chat.llm_client", side_effect=KeyboardInterrupt)
    def test_ctrl_c_during_client_init_exits_cleanly(self, _client):
        self.assertEqual(ai_chat.run(prompt="q"), 0)

    @patch("stocks.ai.chat.llm_client", side_effect=ValueError("bad URL"))
    def test_client_init_error_returns_nonzero(self, _client):
        self.assertEqual(ai_chat.run(prompt="q"), 1)

    @patch("stocks.mood.analyze", return_value=[])
    def test_mood_empty_is_error(self, _mock):
        result = ai_tools.call("mood", {})
        self.assertFalse(result["ok"])

    @patch("stocks.crowd.scan_concept_crowding", return_value=[])
    def test_crowd_empty_is_error(self, _mock):
        self.assertFalse(ai_tools.call("crowd", {})["ok"])

    @patch("stocks.etf.scan_etf_states", return_value=[])
    def test_etf_empty_is_error(self, _mock):
        self.assertFalse(ai_tools.call("etf", {})["ok"])


class TestCliChat(unittest.TestCase):
    def test_chat_in_commands(self):
        self.assertIn("chat", main_controller.COMMANDS)

    @patch("stocks.ai.chat.run", return_value=0)
    def test_main_dispatches_chat(self, mock_run):
        with patch("sys.argv", ["omg", "chat", "今天怎么看", "--research"]):
            code = main_controller.main()
        self.assertEqual(code, 0)
        mock_run.assert_called_once_with(
            prompt="今天怎么看", research=True, skill=None
        )

    @patch("stocks.ai.chat.run", return_value=0)
    def test_main_chat_research_before_prompt(self, mock_run):
        with patch("sys.argv", ["omg", "chat", "--research", "今天怎么看"]):
            code = main_controller.main()
        self.assertEqual(code, 0)
        mock_run.assert_called_once_with(
            prompt="今天怎么看", research=True, skill=None
        )

    @patch("stocks.ai.chat.run", return_value=0)
    def test_main_chat_with_skill(self, mock_run):
        with patch(
            "sys.argv",
            ["omg", "chat", "--research", "--skill", "omg-daily-review", "复盘"],
        ):
            self.assertEqual(main_controller.main(), 0)
        mock_run.assert_called_once_with(
            prompt="复盘", research=True, skill="omg-daily-review"
        )

    @patch("stocks.ai.chat.run", return_value=0)
    def test_chat_double_dash_keeps_option_like_prompt(self, mock_run):
        with patch("sys.argv", ["omg", "chat", "--", "--research", "-k"]):
            self.assertEqual(main_controller.main(), 0)
        mock_run.assert_called_once_with(
            prompt="--research -k", research=False, skill=None
        )

    @patch("stocks.ai.chat.run", return_value=0)
    def test_chat_rejects_global_k_option(self, mock_run):
        with patch("sys.argv", ["omg", "chat", "-k", "半导体"]):
            with self.assertRaises(SystemExit):
                main_controller.main()
        mock_run.assert_not_called()

    def test_k_rejects_unknown_option(self):
        with patch("sys.argv", ["omg", "-k", "半导体", "--bogus"]):
            with self.assertRaises(SystemExit):
                main_controller.main()

    @patch.dict(os.environ, {"OMG_DISABLE_DOTENV": "1"}, clear=True)
    def test_chat_missing_config_nonzero(self):
        with patch("sys.argv", ["omg", "chat"]):
            code = main_controller.main()
        self.assertEqual(code, 1)

    @patch("stocks.ai.chat.ask_once", return_value="结论：冰点")
    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_run_once_returns_assistant_text(self, _client, _ask):
        self.assertEqual(ai_chat.run_once("今天怎么看", research=True), "结论：冰点")
        _ask.assert_called_once()
        self.assertTrue(_ask.call_args.kwargs.get("research"))


class TestSkills(unittest.TestCase):
    def test_list_and_load_daily_review(self):
        from stocks.ai import skills as ai_skills

        names = {s["name"] for s in ai_skills.list_skills()}
        self.assertIn("omg-daily-review", names)
        skill = ai_skills.load("omg-daily-review")
        self.assertIn("日复盘", skill["body"])

    def test_resolve_auto_from_prompt(self):
        from stocks.ai import skills as ai_skills

        for prompt in ("帮我复盘一下今天", "做个日复盘", "今日复盘", "做个复盘"):
            skill = ai_skills.resolve(None, prompt)
            self.assertIsNotNone(skill, prompt)
            self.assertEqual(skill["name"], "omg-daily-review", prompt)

    def test_resolve_avoids_false_positives(self):
        from stocks.ai import skills as ai_skills

        self.assertIsNone(ai_skills.resolve(None, "复盘"))
        self.assertIsNone(ai_skills.resolve(None, "别深挖细节，今天大盘怎么样"))
        self.assertIsNone(ai_skills.resolve(None, "简单说说盘面就行"))
        self.assertIsNone(ai_skills.resolve(None, "用 research 模式随便聊聊"))
        self.assertIsNone(ai_skills.resolve(None, "前日复盘已经写了"))
        self.assertIsNone(ai_skills.resolve(None, "昨日复盘已经写了"))
        self.assertIsNone(ai_skills.resolve(None, "请做多日复盘总结"))
        self.assertIsNone(ai_skills.resolve(None, "两日复盘对比一下"))
        self.assertIsNone(ai_skills.resolve(None, "找个板块研究员聊聊"))
        self.assertIsNone(ai_skills.resolve(None, "我想深挖一下主题性机会"))
        self.assertIsNone(ai_skills.resolve(None, "别帮我复盘了"))
        self.assertIsNone(ai_skills.resolve(None, "别再帮我复盘了"))
        self.assertIsNone(ai_skills.resolve(None, "不要再复盘一下"))
        self.assertIsNone(ai_skills.resolve(None, "不需要复盘一下"))
        self.assertIsNone(ai_skills.resolve(None, "今日复盘已经写了"))
        self.assertIsNone(ai_skills.resolve(None, "深挖一下这个主题性机会"))
        # 复合词里的「别/不是」不应误判为否定
        self.assertEqual(
            ai_skills.resolve(None, "特别帮我复盘一下")["name"],
            "omg-daily-review",
        )
        self.assertEqual(
            ai_skills.resolve(None, "是不是做个复盘")["name"],
            "omg-daily-review",
        )

    def test_resolve_negated_theme_falls_through_to_daily(self):
        from stocks.ai import skills as ai_skills

        skill = ai_skills.resolve(None, "不要主题研究了，帮我复盘一下今天")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["name"], "omg-daily-review")

    def test_theme_beats_daily_when_theme_phrase_present(self):
        from stocks.ai import skills as ai_skills

        skill = ai_skills.resolve(None, "主题研究一下今天的盘面")
        self.assertEqual(skill["name"], "omg-theme-research")

    def test_resolve_theme_spoken_variants(self):
        from stocks.ai import skills as ai_skills

        for prompt in (
            "深挖板块 半导体",
            "深挖一下板块 半导体",
            "深挖一下主题 人形机器人",
            "深挖一下这个主题",
        ):
            skill = ai_skills.resolve(None, prompt)
            self.assertIsNotNone(skill, prompt)
            self.assertEqual(skill["name"], "omg-theme-research", prompt)

    def test_load_unknown_skill_raises(self):
        from stocks.ai import skills as ai_skills
        from stocks.ai.skills import SkillError

        with self.assertRaises(SkillError):
            ai_skills.load("no-such-skill")

    def test_bundled_skills_work_without_project_cursor_directory(self):
        from stocks.ai import skills as ai_skills

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with patch("stocks.ai.skills.skills_root", return_value=missing):
                names = {skill["name"] for skill in ai_skills.list_skills()}
                loaded = ai_skills.load("omg-daily-review")

        self.assertIn("omg-daily-review", names)
        self.assertIn("日复盘", loaded["body"])

    def test_project_and_bundled_skill_copies_stay_in_sync(self):
        from stocks.ai import skills as ai_skills

        for project_path in ai_skills.skills_root().glob("*/SKILL.md"):
            bundled_path = (
                ai_skills.bundled_skills_root()
                / project_path.parent.name
                / project_path.name
            )
            self.assertTrue(bundled_path.is_file(), project_path.parent.name)
            self.assertEqual(
                project_path.read_text(encoding="utf-8"),
                bundled_path.read_text(encoding="utf-8"),
            )

    def test_project_skill_metadata_name_overrides_bundled_skill(self):
        from stocks.ai import skills as ai_skills

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            custom = root / "custom-directory"
            custom.mkdir(parents=True)
            (custom / "SKILL.md").write_text(
                "---\nname: omg-daily-review\n---\nproject override",
                encoding="utf-8",
            )
            with patch("stocks.ai.skills.skills_root", return_value=root):
                loaded = ai_skills.load("omg-daily-review")

        self.assertEqual(loaded["body"], "project override")

    def test_skill_name_cannot_escape_skills_root(self):
        from stocks.ai import skills as ai_skills
        from stocks.ai.skills import SkillError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "SKILL.md").write_text(
                "---\nname: outside\n---\nsecret",
                encoding="utf-8",
            )
            with patch("stocks.ai.skills.skills_root", return_value=root):
                with self.assertRaises(SkillError):
                    ai_skills.load("../outside")

    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_run_once_unknown_skill_raises(self, _client):
        from stocks.ai.skills import SkillError

        with self.assertRaises(SkillError):
            ai_chat.run_once("随便问", skill="no-such-skill")

    @patch("stocks.ai.chat.ask_once", return_value="ok")
    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_run_once_auto_theme_enables_research(self, _client, mock_ask):
        ai_chat.run_once("深挖一下板块 半导体", research=False)
        self.assertTrue(mock_ask.call_args.kwargs.get("research"))
        messages = mock_ask.call_args.args[2]
        self.assertTrue(
            any(
                "omg-theme-research" in (m.get("content") or "")
                for m in messages
                if m.get("role") == "system"
            )
        )

    @patch("stocks.ai.chat.run")
    def test_main_chat_list_skills(self, mock_run):
        buf = io.StringIO()
        with patch("sys.argv", ["omg", "chat", "--list-skills"]):
            with patch("sys.stdout", buf):
                code = main_controller.main()
        self.assertEqual(code, 0)
        mock_run.assert_not_called()
        self.assertIn("omg-daily-review", buf.getvalue())
        self.assertIn("omg-theme-research", buf.getvalue())

    @patch("stocks.ai.chat.run", return_value=0)
    def test_main_chat_skill_equals_form(self, mock_run):
        with patch(
            "sys.argv",
            ["omg", "chat", "--skill=omg-daily-review", "做个复盘"],
        ):
            self.assertEqual(main_controller.main(), 0)
        mock_run.assert_called_once_with(
            prompt="做个复盘", research=False, skill="omg-daily-review"
        )

    @patch("stocks.ai.chat.ask_once", return_value="ok")
    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_run_once_injects_skill_system(self, _client, mock_ask):
        ai_chat.run_once("随便问", research=False, skill="omg-daily-review")
        messages = mock_ask.call_args.args[2]
        skill_msgs = [
            m
            for m in messages
            if m.get("role") == "system" and "omg-daily-review" in (m.get("content") or "")
        ]
        self.assertTrue(skill_msgs)
        self.assertTrue(mock_ask.call_args.kwargs.get("research"))
        self.assertTrue(
            any(
                m.get("content") == ai_chat.SYSTEM_RESEARCH
                for m in messages
                if m.get("role") == "system"
            )
        )

    @patch("stocks.ai.chat.ask_once", return_value="ok")
    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_repl_auto_skill_on_first_input(self, _client, mock_ask):
        with patch("builtins.input", side_effect=["帮我复盘一下今天", "exit"]):
            code = ai_chat.run()
        self.assertEqual(code, 0)
        messages = mock_ask.call_args.args[2]
        self.assertTrue(
            any(
                "omg-daily-review" in (m.get("content") or "")
                for m in messages
                if m.get("role") == "system"
            )
        )
        self.assertTrue(mock_ask.call_args.kwargs.get("research"))


if __name__ == "__main__":
    unittest.main()
