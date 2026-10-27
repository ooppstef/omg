import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from stocks import review_html
from stocks.ai import chat as ai_chat


def minimal_body() -> str:
    sections = "".join(
        f'<section class="section" id="{section}"><h2>{section}</h2></section>'
        for section in review_html.SECTION_IDS
    )
    return (
        '<header class="wrap masthead">head</header>'
        '<nav class="toc">nav</nav>'
        '<main id="main" class="wrap">' + sections + "</main>"
        '<footer class="wrap footer">foot</footer>'
    )


class TestReviewHTML(unittest.TestCase):
    def test_template_has_single_placeholders(self):
        shell = review_html.load_template()
        self.assertEqual(shell.count("{{TITLE}}"), 1)
        self.assertEqual(shell.count("{{BODY}}"), 1)

    def test_render_body_injects_shell_and_title(self):
        html = review_html.render_body(minimal_body(), title="测试复盘")
        self.assertIn("<title>测试复盘</title>", html)
        self.assertIn('id="conclusion"', html)
        self.assertNotIn("{{TITLE}}", html)
        self.assertNotIn("{{BODY}}", html)
        self.assertEqual(html.count('id="to-top"'), 1)
        self.assertEqual(html.count('id="progress-bar"'), 1)

    def test_render_body_rejects_missing_section(self):
        body = minimal_body().replace('id="rank"', 'id="not-rank"')
        with self.assertRaises(review_html.ReviewHTMLError):
            review_html.validate_body(body)

    def test_render_body_rejects_duplicate_section(self):
        body = minimal_body().replace(
            '<footer class="wrap footer">foot</footer>',
            '<section class="section" id="market"></section>'
            '<footer class="wrap footer">foot</footer>',
        )
        with self.assertRaises(review_html.ReviewHTMLError):
            review_html.validate_body(body)

    def test_render_body_rejects_script(self):
        body = minimal_body().replace(
            '<footer class="wrap footer">foot</footer>',
            '<footer class="wrap footer">foot</footer><script>alert(1)</script>',
        )
        with self.assertRaises(review_html.ReviewHTMLError):
            review_html.validate_body(body)

    def test_render_body_rejects_fixed_chrome(self):
        body = minimal_body().replace(
            '<header class="wrap masthead">head</header>',
            '<a class="skip" href="#main">skip</a>'
            '<header class="wrap masthead">head</header>',
        )
        with self.assertRaises(review_html.ReviewHTMLError):
            review_html.validate_body(body)

    def test_markdown_fallback_has_all_sections(self):
        md = """## 结论
- **结构**：主题抱团。

## 依据
量能缺口。

## 涨跌榜
机器人。

## 近期强势
电机Ⅱ +6.90%。

## 自选操作建议
不宜低吸。

## 底部启动
只跟踪。

## 推荐关注
观察。

## 风险
炸板率 41%。

## 操作建议
宜等。

## 关注建议
涨停数 >60。
"""
        html = review_html.render_markdown(
            md, title="Markdown 复盘", data_date="2026-10-27"
        )
        for section in review_html.SECTION_IDS:
            self.assertIn(f'id="{section}"', html)
        self.assertIn("主题抱团", html)
        self.assertIn("<strong>", html)

    def test_archive_uses_run_date_folder_and_data_date_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "review.html"
            source.write_text("x", encoding="utf-8")
            dest = review_html.archive(
                source,
                "2026-10-27",
                run_date="2026-10-28",
                desktop=root,
            )
            self.assertEqual(
                dest,
                root / "2026_10_28_复盘" / "2026_10_27_复盘.html",
            )
            self.assertTrue(dest.is_file())

    def test_deliver_writes_work_copy_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output" / "review.html"
            dest = review_html.deliver(
                "## 结论\n- 测试。\n",
                title="交付测试",
                data_date="2026-10-27",
                output=output,
                desktop=root / "Desktop",
                run_date="2026-10-27",
            )
            self.assertTrue(output.is_file())
            self.assertTrue(dest.is_file())
            self.assertEqual(
                dest,
                root / "Desktop" / "2026_10_27_复盘" / "2026_10_27_复盘.html",
            )

    def test_should_deliver(self):
        self.assertFalse(review_html.should_deliver("ok"))
        self.assertTrue(
            review_html.should_deliver("## 结论\n" + "很长的复盘内容。" * 40)
        )
        with patch.dict(os.environ, {"OMG_CHAT_HTML": "0"}):
            self.assertFalse(
                review_html.should_deliver("## 结论\n" + "很长的复盘内容。" * 40)
            )

    def test_bundled_skill_documents_html_delivery(self):
        body = Path(
            "stocks/ai/bundled_skills/omg-daily-review/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("HTML 交付（默认）", body)
        self.assertIn("render_daily_review.py", body)
        for section in review_html.SECTION_IDS:
            self.assertIn(f'id="{section}"', body)

    def test_agents_md_documents_html_delivery(self):
        text = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("固定版式 HTML", text)
        self.assertIn("render_daily_review.py", text)
        self.assertIn(".cursor/", text)

    def test_render_cli_writes_work_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = root / "body.html"
            body.write_text(minimal_body(), encoding="utf-8")
            output = root / "review.html"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/render_daily_review.py",
                    str(body),
                    "--title",
                    "CLI 测试",
                    "--date",
                    "2026-10-27",
                    "--output",
                    str(output),
                    "--no-archive",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertIn('id="conclusion"', output.read_text(encoding="utf-8"))


class TestChatHTMLReview(unittest.TestCase):
    def test_bootstrap_adds_html_system_for_daily_review(self):
        with patch(
            "stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo")
        ):
            _, _, messages, research = ai_chat._bootstrap(
                False,
                skill={"name": "omg-daily-review", "body": "x"},
            )
        self.assertTrue(research)
        self.assertTrue(
            any(
                message.get("content") == ai_chat.SYSTEM_HTML_REVIEW
                for message in messages
                if message.get("role") == "system"
            )
        )

    @patch("stocks.ai.chat._deliver_html_review", return_value="/tmp/review.html")
    @patch("stocks.ai.chat.ask_once", return_value="## 结论\n" + "内容。" * 100)
    @patch("stocks.ai.chat.llm_client", return_value=(MagicMock(), "demo"))
    def test_run_once_returns_html_path_for_daily_review(
        self, _client, _ask, _deliver
    ):
        result = ai_chat.run_once("今日复盘", skill="omg-daily-review")
        self.assertEqual(result, "已生成 HTML 日复盘：/tmp/review.html")
        _deliver.assert_called_once()


if __name__ == "__main__":
    unittest.main()
