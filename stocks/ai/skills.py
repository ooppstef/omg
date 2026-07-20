"""加载内置或项目 .cursor/skills 中的 SKILL.md，供 omg chat 注入 system。"""

from __future__ import annotations

import re
from pathlib import Path

# 更具体的短语优先；避免「深挖」「盘面」等裸子串误触发
_AUTO_TRIGGERS = (
    (
        (
            "主题研究",
            "板块研究",
            "深挖板块",
            "深挖一下板块",
            "深挖一下主题",
            "深挖一下这个主题",
        ),
        "omg-theme-research",
    ),
    (
        (
            "帮我复盘",
            "复盘一下",
            "今日复盘",
            "做个日复盘",
            "今天怎么看",
            "做个复盘",
        ),
        "omg-daily-review",
    ),
)

# 挂上这些 skill 时默认打开 research（更多 tool 预算 + research system）
_RESEARCH_SKILLS = frozenset({"omg-daily-review", "omg-theme-research"})


class SkillError(ValueError):
    """skill 不存在或无效。"""


def skills_root() -> Path:
    return Path(__file__).resolve().parents[2] / ".cursor" / "skills"


def bundled_skills_root() -> Path:
    return Path(__file__).resolve().parent / "bundled_skills"


def _skill_roots() -> list[Path]:
    roots = []
    for root in (skills_root(), bundled_skills_root()):
        if root.is_dir() and root not in roots:
            roots.append(root)
    return roots


def list_skills() -> list[dict[str, str]]:
    rows_by_name = {}
    for root in _skill_roots():
        for path in sorted(root.glob("*/SKILL.md")):
            meta, _ = _parse(path.read_text(encoding="utf-8"))
            name = meta.get("name") or path.parent.name
            rows_by_name.setdefault(
                name,
                {
                    "name": name,
                    "description": meta.get("description") or "",
                    "path": str(path),
                },
            )
    return [rows_by_name[name] for name in sorted(rows_by_name)]


def load(name: str) -> dict[str, str]:
    """按目录名或 frontmatter name 加载 skill；找不到则抛 SkillError。"""
    key = (name or "").strip()
    if not key:
        raise SkillError("skill 名不能为空")
    if key in {".", ".."} or "/" in key or "\\" in key or "\0" in key:
        raise SkillError("skill 名不能包含路径分隔符")
    roots = _skill_roots()
    for root in roots:
        candidates = [
            root / key / "SKILL.md",
            root / key.replace("_", "-") / "SKILL.md",
        ]
        for path in candidates:
            if path.is_file():
                return _skill_from_path(path)
        for path in root.glob("*/SKILL.md"):
            meta, body = _parse(path.read_text(encoding="utf-8"))
            if (meta.get("name") or "").strip() == key:
                return {
                    "name": meta.get("name") or path.parent.name,
                    "description": meta.get("description") or "",
                    "body": body,
                    "path": str(path),
                }
    known = ", ".join(s["name"] for s in list_skills()) or "(无)"
    raise SkillError(f"未知 skill: {key}；可用: {known}")


def resolve(name: str | None = None, prompt: str | None = None) -> dict[str, str] | None:
    """显式名优先；否则按首问关键词自动匹配。"""
    if name and name.strip():
        return load(name.strip())
    text = prompt or ""
    for keys, skill_name in _AUTO_TRIGGERS:
        if any(_phrase_hit(text, k) for k in keys):
            try:
                return load(skill_name)
            except SkillError:
                return None
    return None


# 否定词须紧贴触发短语（中间最多语气/标点），避免「不要主题…，帮我复盘」误伤后半句。
# 「别/不是」加左边界，避免命中「特别」「是不是」。
_NEG_BEFORE = re.compile(
    r"(?:不要|先别|不需要|无需|不用|(?<!是)不是|(?<![特个])别)"
    r"(?:再)?[了啊呢吧吗，,、\s]{0,3}$"
)
_DONE_AFTER = re.compile(r"^.{0,4}已经")


def _phrase_hit(text: str, phrase: str) -> bool:
    """子串匹配；跳过紧邻否定/完成态，并对易误伤复合词做约束。"""
    start = 0
    while True:
        idx = text.find(phrase, start)
        if idx < 0:
            return False
        end = idx + len(phrase)
        prefix = text[max(0, idx - 8) : idx]
        suffix = text[end : end + 8]
        if _NEG_BEFORE.search(prefix) or _DONE_AFTER.search(suffix):
            start = end
            continue
        # 「板块研究」勿命中 板块研究员/部/所
        if phrase == "板块研究" and re.match(r"[员部所院生]", suffix):
            start = end
            continue
        # 「深挖一下(这个)主题」勿命中 …主题性…
        if phrase in ("深挖一下主题", "深挖一下这个主题") and suffix.startswith("性"):
            start = end
            continue
        return True


def wants_research(skill: dict[str, str] | None) -> bool:
    if not skill:
        return False
    return (skill.get("name") or "") in _RESEARCH_SKILLS


def _skill_from_path(path: Path) -> dict[str, str]:
    meta, body = _parse(path.read_text(encoding="utf-8"))
    return {
        "name": meta.get("name") or path.parent.name,
        "description": meta.get("description") or "",
        "body": body,
        "path": str(path),
    }


def _parse(text: str) -> tuple[dict[str, str], str]:
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta: dict[str, str] = {}
    raw_meta = parts[1]
    key = None
    buf: list[str] = []
    for line in raw_meta.splitlines():
        if re.match(r"^[A-Za-z0-9_-]+:\s*", line):
            if key is not None:
                meta[key] = " ".join(buf).strip()
            name, _, rest = line.partition(":")
            key = name.strip()
            rest = rest.strip()
            if rest == ">":
                buf = []
            else:
                buf = [rest] if rest else []
        elif key is not None:
            buf.append(line.strip())
    if key is not None:
        meta[key] = " ".join(buf).strip()
    body = parts[2].strip()
    return meta, body
