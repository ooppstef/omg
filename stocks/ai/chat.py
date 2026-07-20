"""REPL 与 tool loop。"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from stocks.ai import skills as ai_skills
from stocks.ai import tools as ai_tools
from stocks.ai.llm import ConfigError, client as llm_client
from stocks.ai.skills import SkillError

HISTORY_KEEP = 20
MAX_TOOLS = 8
MAX_TOOLS_RESEARCH = 16
# 推理模型会占用大量 completion tokens；过小会导致正文为空
MAX_COMPLETION_TOKENS = 8192

SYSTEM = """你是 A 股行情助手，运行在 omg chat 中。
规则：
1. 行情结论必须来自 tools（mood/style/rot/crowd/etf/hot/list），不要自行编造涨跌幅或阶段。
2. 需要外部新闻/公告时用 web_search。
3. 用中文；先结论，再依据、风险，并可给操作建议与关注建议。信息要密：名称 + 1～2 个关键数字/对比。
4. 控制篇幅：宁短勿水。不要大段表格或逐日罗列；只保留拐点/极值/代表项。
5. 操作/关注建议必须能回溯到刚取到的 tool 证据；用「宜/不宜/优先观察」表述。
6. 禁止：具体仓位比例、目标价、下单指令、保证收益话术。这不是投顾荐股。
"""

SYSTEM_RESEARCH = """当前为 research 模式：
1. 先规划维度，再尽量多调用 tools / web_search 取证，缺口再补调。
2. 最终输出五节：## 结论 / ## 依据 / ## 风险 / ## 操作建议 / ## 关注建议；注明用过的 tool。
3. 密度：结论 3～5 句；依据每维 2～4 句；风险 3～5 条；操作建议 3～5 条；关注建议 3～5 条。
4. 操作建议=基于证据的行为倾向（如不宜追拥挤科技、可偏向红利/防御观察、等待情绪拐点确认等），不要写成买卖点。
5. 关注建议=接下来盯什么（涨停家数、某行业拥挤分位、代表 ETF 状态、自选背离等），尽量可验证。
6. 禁止空泛定调、完整多日大表、ETF 全清单、仓位%/目标价。冲突结构必须写明。
"""

_LIMIT_RESULT = {"ok": False, "data": None, "error": "已达本轮 tool 调用上限"}


def _assistant_message(msg) -> dict[str, Any]:
    item: dict[str, Any] = {"role": "assistant", "content": msg.content or None}
    if msg.tool_calls:
        item["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in msg.tool_calls
        ]
    return item


def trim_messages(messages: list[dict], keep: int = HISTORY_KEEP) -> list[dict]:
    """保留全部 system + 最近约 keep 条，不拆分当前对话轮次。"""
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    start = max(0, len(rest) - keep)
    if start and rest[start].get("role") != "user":
        # 优先丢掉被窗口切开的旧轮次。
        next_user = next(
            (i for i in range(start + 1, len(rest)) if rest[i].get("role") == "user"),
            None,
        )
        if next_user is not None:
            start = next_user
        else:
            # 当前轮本身超过窗口时，完整保留该轮，避免拆散 tool_calls。
            while start > 0 and rest[start].get("role") != "user":
                start -= 1
    return system + rest[start:]


def _parse_args(raw: str | None) -> tuple[dict | None, str | None]:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        return None, f"工具参数不是有效 JSON: {e.msg}"
    if not isinstance(args, dict):
        return None, "工具参数必须是 JSON 对象"
    return args, None


def _message_text(msg) -> str:
    text = (msg.content or "").strip()
    if text:
        return text
    # 部分推理模型可能把草稿放在 reasoning_content
    reasoning = getattr(msg, "reasoning_content", None)
    return (reasoning or "").strip()


def _final_text(oai, model: str, messages: list[dict]) -> str:
    resp = oai.chat.completions.create(
        model=model,
        messages=trim_messages(messages),
        max_tokens=MAX_COMPLETION_TOKENS,
    )
    final = resp.choices[0].message
    text = _message_text(final)
    messages.append(_assistant_message(final))
    return text


def ask_once(
    oai,
    model: str,
    messages: list[dict],
    *,
    research: bool = False,
    max_tool_calls: Optional[int] = None,
) -> str:
    """单轮用户问题的 tool loop，返回最终助手文本。会原地追加 messages。"""
    limit = max_tool_calls
    if limit is None:
        limit = MAX_TOOLS_RESEARCH if research else MAX_TOOLS
    tool_schemas = ai_tools.schemas()
    used = 0

    while True:
        messages[:] = trim_messages(messages)
        resp = oai.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas,
            max_tokens=MAX_COMPLETION_TOKENS,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            text = _message_text(msg)
            messages.append(_assistant_message(msg))
            return text

        # 必须为每个 tool_call 写入一条 tool 消息，否则违反 API 契约
        messages.append(_assistant_message(msg))
        for tc in msg.tool_calls:
            if used >= limit:
                result = _LIMIT_RESULT
            else:
                used += 1
                args, error = _parse_args(tc.function.arguments)
                result = (
                    {"ok": False, "data": None, "error": error}
                    if error
                    else ai_tools.call(tc.function.name, args)
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": ai_tools.dumps(result),
                }
            )

        if used >= limit:
            return _final_text(oai, model, messages)


def _skill_system(skill: dict[str, str]) -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            f"已启用 skill「{skill.get('name', '')}」。"
            "你已在 omg chat 的 tool loop 中：直接调用已注册 function tools，"
            "不要再执行 omg CLI、run_once，或 import stocks.ai.tools。"
            "以下文档里的「如何启动 / When to Use / CLI 示例」仅供人类参考；"
            "请按其中的取证维度/流程、综合规则与输出模板执行。\n\n"
            f"{skill['body']}"
        ),
    }


def _has_system(messages: list[dict], content: str) -> bool:
    return any(m.get("role") == "system" and m.get("content") == content for m in messages)


def _ensure_research_system(messages: list[dict]) -> None:
    if _has_system(messages, SYSTEM_RESEARCH):
        return
    # 插在基础 SYSTEM 之后
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    messages.insert(insert_at, {"role": "system", "content": SYSTEM_RESEARCH})


def _attach_skill(messages: list[dict], skill: dict[str, str]) -> None:
    marker = f"已启用 skill「{skill.get('name', '')}」"
    if any(
        m.get("role") == "system" and marker in (m.get("content") or "")
        for m in messages
    ):
        return
    messages.append(_skill_system(skill))


def _bootstrap(
    research: bool,
    *,
    skill: Optional[dict[str, str]] = None,
) -> tuple[object, str, list[dict], bool]:
    """返回 (client, model, messages, research)；挂日复盘/主题研究 skill 时自动打开 research。"""
    if ai_skills.wants_research(skill):
        research = True
    oai, model = llm_client()
    messages: list[dict] = [{"role": "system", "content": SYSTEM}]
    if research:
        messages.append({"role": "system", "content": SYSTEM_RESEARCH})
    if skill and skill.get("body"):
        messages.append(_skill_system(skill))
    return oai, model, messages, research


def run_once(
    prompt: str,
    *,
    research: bool = False,
    skill: Optional[str] = None,
) -> str:
    """单轮非交互：返回助手最终文本。

    配置错误抛 ConfigError；无效 skill 名抛 SkillError（REPL/CLI 则捕获并 exit 2）。
    """
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt 不能为空")
    loaded = ai_skills.resolve(skill, text)
    oai, model, messages, research = _bootstrap(research, skill=loaded)
    messages.append({"role": "user", "content": text})
    return ask_once(oai, model, messages, research=research)


def run(
    prompt: Optional[str] = None,
    research: bool = False,
    skill: Optional[str] = None,
) -> int:
    """交互 REPL。Ctrl-C → 0；配置错误 → 1；无效 skill → 2。"""
    first = (prompt or "").strip() or None
    explicit_skill = bool(skill and str(skill).strip())
    try:
        loaded = ai_skills.resolve(skill, first)
        oai, model, messages, research = _bootstrap(research, skill=loaded)
    except SkillError as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 0
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"LLM 初始化失败: {e}", file=sys.stderr)
        return 1

    mode = "research" if research else "normal"
    skill_tag = f" · skill={loaded['name']}" if loaded else ""
    print(f"omg chat（{mode}）{skill_tag} · Ctrl-C 退出 · model={model}")

    request_failed = False
    try:
        while True:
            if first is not None:
                user_text = first
                first = None
            else:
                try:
                    user_text = input("你> ").strip()
                except EOFError:
                    print()
                    return 1 if request_failed else 0
            if not user_text:
                continue
            if user_text.lower() in ("exit", "quit"):
                return 0

            # 无 CLI 首问时：第一条用户输入再尝试自动挂 skill
            if loaded is None and not explicit_skill:
                loaded = ai_skills.resolve(None, user_text)
                if loaded:
                    _attach_skill(messages, loaded)
                    if ai_skills.wants_research(loaded):
                        research = True
                        _ensure_research_system(messages)
                    print(f"（已启用 skill={loaded['name']}，research={'on' if research else 'off'}）")

            messages.append({"role": "user", "content": user_text})
            print("…", flush=True)
            try:
                answer = ask_once(oai, model, messages, research=research)
            except KeyboardInterrupt:
                print()
                return 0
            except Exception as e:
                print(f"（请求失败: {e}）", flush=True)
                while messages and messages[-1].get("role") != "user":
                    messages.pop()
                if messages and messages[-1].get("role") == "user":
                    messages.pop()
                request_failed = True
                continue
            request_failed = False
            print(f"助手> {answer}\n")
    except KeyboardInterrupt:
        print()
        return 0
