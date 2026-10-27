"""固定版式日复盘 HTML：校验、渲染、归档。

设计约定：
- `stocks/templates/daily_review.html` 是固定外壳（CSS/JS），只留 `{{TITLE}}` 与 `{{BODY}}`。
- `BODY` 必须包含 8 个 section：conclusion / market / themes / rank / strength /
  watch / bottom / board，且自带 masthead、toc、main、footer。
- 没有 body 时，可把 Markdown 复盘转换成简化版 HTML（omg chat 兜底）。
"""

from __future__ import annotations

import html as html_lib
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

SECTION_IDS = (
    "conclusion",
    "market",
    "themes",
    "rank",
    "strength",
    "watch",
    "bottom",
    "board",
)
SECTION_TITLES = {
    "conclusion": "结论",
    "market": "盘面仪表",
    "themes": "主线与轮动",
    "rank": "涨跌榜",
    "strength": "近期强势",
    "watch": "自选操作建议",
    "bottom": "底部启动与推荐关注",
    "board": "风险与动作",
}
_TEMPLATE = Path(__file__).with_name("templates") / "daily_review.html"
_SECTION_RE = re.compile(
    r"""<section\b[^>]*\bid\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)
_FORBIDDEN_BODY = ("<html", "<body", "<script", "</body", "</html>")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_ORDERED_RE = re.compile(r"^\d+[.)]\s+(.+)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.+)$")


class ReviewHTMLError(ValueError):
    """HTML 复盘内容不符合固定版式。"""


def load_template() -> str:
    try:
        return _TEMPLATE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # pragma: no cover - packaging failure
        raise ReviewHTMLError(f"HTML 模板不存在: {_TEMPLATE}") from exc


def validate_body(body: str) -> list[str]:
    """校验 body fragment，返回实际 section id 列表。"""
    if not isinstance(body, str) or not body.strip():
        raise ReviewHTMLError("HTML body 不能为空")
    lower = body.lower()
    for tag in _FORBIDDEN_BODY:
        if tag in lower:
            raise ReviewHTMLError(
                "HTML body 只能包含片段：不要包含 <html> / <body> / <script>"
            )
    found = _SECTION_RE.findall(body)
    missing = [section for section in SECTION_IDS if section not in found]
    if missing:
        raise ReviewHTMLError(
            "HTML body 缺少 section: " + ", ".join(missing)
        )
    duplicates = sorted({section for section in found if found.count(section) > 1})
    if duplicates:
        raise ReviewHTMLError("HTML body section 重复: " + ", ".join(duplicates))
    for chrome in ('class="skip"', 'id="progress-bar"', 'id="to-top"'):
        if chrome in lower:
            raise ReviewHTMLError(
                "HTML body 不要包含 skip / progress / to-top；固定外壳已提供"
            )
    for marker in ("masthead", "toc", "footer"):
        if marker not in lower:
            raise ReviewHTMLError(f"HTML body 缺少固定版式元素: {marker}")
    if not re.search(r"""<main\b[^>]*\bid\s*=\s*["']main["']""", body, re.I):
        raise ReviewHTMLError('HTML body 缺少 <main id="main">')
    return found


def render_body(body: str, *, title: str) -> str:
    """把 body fragment 注入固定外壳。"""
    validate_body(body)
    shell = load_template()
    safe_title = html_lib.escape(str(title), quote=False)
    if shell.count("{{TITLE}}") != 1 or shell.count("{{BODY}}") != 1:
        raise ReviewHTMLError("HTML 模板必须各含一个 {{TITLE}} / {{BODY}} 占位符")
    rendered = shell.replace("{{TITLE}}", safe_title).replace("{{BODY}}", body)
    if "{{TITLE}}" in rendered or "{{BODY}}" in rendered:  # pragma: no cover
        raise ReviewHTMLError("HTML 模板占位符未完全替换")
    return rendered


def _inline_markdown(text: str) -> str:
    text = html_lib.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        return f'<a href="{html_lib.escape(url, quote=True)}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)


def markdown_to_html(markdown: str) -> str:
    """最小 Markdown 转换：标题、列表、引用、代码块、粗体、行内代码、链接。"""
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + _inline_markdown(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                out.append(
                    "<pre><code>" + html_lib.escape("\n".join(code_lines)) + "</code></pre>"
                )
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            close_list()
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)) + 1, 4)
            out.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            close_list()
            out.append(
                "<blockquote>"
                + _inline_markdown(stripped.lstrip("> ").strip())
                + "</blockquote>"
            )
            continue
        bullet = _BULLET_RE.match(stripped)
        ordered = _ORDERED_RE.match(stripped)
        if bullet or ordered:
            flush_paragraph()
            kind = "ul" if bullet else "ol"
            if list_kind != kind:
                close_list()
                out.append(f"<{kind}>")
                list_kind = kind
            item = (bullet or ordered).group(1)
            out.append(f"<li>{_inline_markdown(item)}</li>")
            continue
        close_list()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    if in_code:
        out.append("<pre><code>" + html_lib.escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(out)


def _section_for_heading(heading: str) -> str:
    text = heading.strip()
    if "结论" in text:
        return "conclusion"
    if "依据" in text or "盘面" in text or "市场" in text:
        return "market"
    if "主线" in text or "轮动" in text or "题材" in text:
        return "themes"
    if "涨跌" in text:
        return "rank"
    if "强势" in text:
        return "strength"
    if "自选" in text:
        return "watch"
    if "底部" in text or "推荐" in text or "观察" in text:
        return "bottom"
    if "风险" in text or "操作" in text or "关注" in text:
        return "board"
    return "conclusion"


def split_markdown_sections(markdown: str) -> dict[str, str]:
    """按 `## 标题` 把 Markdown 拆到固定 8 section；未命中的内容归结论。

    每个 section 的第一个标题由固定版式的 h2 承担，不再重复输出；同一
    section 的后续标题（如「推荐关注」「操作建议」）保留为子标题。
    """
    sections: dict[str, list[str]] = {section: [] for section in SECTION_IDS}
    seen: set[str] = set()
    current = "conclusion"
    for line in (markdown or "").replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = _section_for_heading(heading.group(1))
            if current in seen:
                sections[current].append(line)
            else:
                seen.add(current)
            continue
        sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _fallback_masthead(title: str, data_date: date) -> str:
    return f"""<header class="wrap masthead">
  <div class="masthead-top">
    <span class="kicker">OMG · A 股日复盘 · {data_date:%Y.%m.%d}</span>
    <span class="edition">数据日 {data_date:%Y-%m-%d} 收盘 · HTML 自动生成</span>
  </div>
  <div class="masthead-seal" aria-hidden="true">
    <div class="seal"><span>复</span><span>盘</span></div>
    <small>{data_date:%Y.%m.%d}</small>
  </div>
  <h1>{html_lib.escape(title, quote=False)}</h1>
  <p class="dek">由 omg chat 自动整理并渲染为固定 HTML；状态、分数、<code>action</code> 均为规则标签，不是收益预测。</p>
  <div class="masthead-note">
    <span>完整逐票明细以正文为准；数据缺口已在页内标注。</span>
    <span class="mono">数据日 {data_date:%Y-%m-%d}</span>
  </div>
</header>"""


def _fallback_nav() -> str:
    links = "".join(
        f'<a href="#{section}">{SECTION_TITLES[section]}</a>'
        for section in SECTION_IDS
    )
    return (
        '<nav class="toc" aria-label="章节导航"><div class="toc-inner">'
        f'<div class="toc-links">{links}</div>'
        '<div class="toc-actions"><button class="tbtn" id="toggle-details" type="button">展开明细</button>'
        '<button class="tbtn" id="theme" type="button">夜间</button>'
        '<button class="tbtn" id="print" type="button">打印</button></div>'
        "</div></nav>"
    )


def render_markdown(markdown: str, *, title: str, data_date: str | date) -> str:
    """把 Markdown 复盘渲染成简化但完整的固定 HTML（omg chat 兜底）。"""
    target = parse_date(data_date)
    parts = split_markdown_sections(markdown)
    sections: list[str] = []
    for index, section in enumerate(SECTION_IDS, start=1):
        content = markdown_to_html(parts.get(section, "")) or (
            '<p class="muted">本轮该维度未返回可用内容（缺口）。</p>'
        )
        sections.append(
            f"""  <section class="section" id="{section}">
    <div class="section-head"><span class="section-num">{index:02d}</span><div><h2>{SECTION_TITLES[section]}</h2></div></div>
    <article class="card"><div class="md-body">{content}</div></article>
  </section>"""
        )
    body = "\n".join(
        [
            _fallback_masthead(title, target),
            _fallback_nav(),
            '<main id="main" class="wrap">',
            *sections,
            "</main>",
            '<footer class="wrap footer"><div class="footer-grid">'
            "<div><h4>数据来源</h4><p>omg tools 与公开资料；由 omg 自动渲染为 HTML。</p></div>"
            "<div><h4>口径</h4><p>状态/分数为规则标签，<code>space</code>/<code>action</code>/<code>s_risk</code> 非收益预测；缺口已在页内标注。</p></div>"
            "</div><p class=\"disclaimer\">仅供学习、研究与个人工作流自动化，不构成投资建议或交易指令。</p></footer>",
        ]
    )
    return render_body(body, title=title)


def parse_date(value: str | date | datetime | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y_%m_%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ReviewHTMLError(f"无法识别日期: {value}")


def desktop_dir() -> Path:
    home = Path.home()
    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            value = os.environ.get(env)
            if value:
                candidates.append(Path(value) / "Desktop")
        profile = os.environ.get("USERPROFILE")
        if profile:
            candidates.append(Path(profile) / "Desktop")
        candidates.append(home / "Desktop")
    else:
        xdg = os.environ.get("XDG_DESKTOP_DIR")
        if xdg:
            candidates.append(Path(xdg).expanduser())
        candidates.append(home / "Desktop")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    fallback = home / "Desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def archive(
    source: str | Path,
    data_date: str | date | datetime,
    *,
    run_date: str | date | datetime | None = None,
    desktop: str | Path | None = None,
) -> Path:
    """复制到桌面：<运行日>_复盘/<数据日>_复盘.html。"""
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise ReviewHTMLError(f"待归档文件不存在: {src}")
    target_data = parse_date(data_date)
    target_run = parse_date(run_date)
    root = Path(desktop).expanduser() if desktop else desktop_dir()
    folder = root / f"{target_run:%Y_%m_%d}_复盘"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{target_data:%Y_%m_%d}_复盘.html"
    if src != dest.resolve():
        shutil.copy2(src, dest)
    return dest


def looks_like_body(text: str) -> bool:
    found = set(_SECTION_RE.findall(text or ""))
    return all(section in found for section in SECTION_IDS)


def should_deliver(text: str) -> bool:
    """omg chat 是否应把本轮回答落成 HTML。短测试/闲聊不落盘。"""
    if (os.environ.get("OMG_CHAT_HTML") or "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    body = text or ""
    if looks_like_body(body):
        return True
    return len(body) >= 300 and "## " in body


def deliver(
    content: str,
    *,
    title: str,
    data_date: str | date | datetime,
    output: str | Path = "output/review.html",
    archive_file: bool = True,
    desktop: str | Path | None = None,
    run_date: str | date | datetime | None = None,
    mode: str = "auto",
) -> Path:
    """把 HTML body 或 Markdown 复盘渲染、落盘并归档，返回最终路径。"""
    if mode not in {"auto", "body", "markdown"}:
        raise ReviewHTMLError(f"未知渲染模式: {mode}")
    target = parse_date(data_date)
    if mode == "body" or (mode == "auto" and looks_like_body(content)):
        rendered = render_body(content, title=title)
    else:
        rendered = render_markdown(content, title=title, data_date=target)
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    if archive_file:
        return archive(out, target, run_date=run_date, desktop=desktop)
    return out


def open_file(path: str | Path) -> None:
    """尽力打开归档文件；失败不影响主流程。"""
    import subprocess

    target = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", target], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", target], check=False)
    except Exception:
        pass
