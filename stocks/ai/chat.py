"""REPL 与 tool loop。"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from stocks.ai import tools as ai_tools
from stocks.ai.llm import ConfigError, client as llm_client

HISTORY_KEEP = 20
MAX_TOOLS = 8
MAX_TOOLS_RESEARCH = 16
# 推理模型会占用大量 completion tokens；过小会导致正文为空
MAX_COMPLETION_TOKENS = 8192

SYSTEM = """你是 A 股行情助手，运行在 omg chat 中。
规则：
1. 行情结论必须来自 tools（mood/style/rot/crowd/etf/hot/list），不要自行编造涨跌幅或阶段。
2. 需要外部新闻/公告时用 web_search。
3. 回答简洁，用中文；先给结论，再列依据与风险。
4. 不做交易下单建议的具体仓位指令；可给观察维度与风险提示。
"""

SYSTEM_RESEARCH = """当前为 research 模式：先规划需要的维度，再尽量多调用 tools / web_search 取证，发现缺口再补调，最后输出结论、依据（注明用过的 tool）与风险点。不要空口结论。
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


def _bootstrap(research: bool) -> tuple[object, str, list[dict]]:
    oai, model = llm_client()
    messages: list[dict] = [{"role": "system", "content": SYSTEM}]
    if research:
        messages.append({"role": "system", "content": SYSTEM_RESEARCH})
    return oai, model, messages


def run_once(prompt: str, *, research: bool = False) -> str:
    """单轮非交互：返回助手最终文本。配置错误抛 ConfigError。"""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt 不能为空")
    oai, model, messages = _bootstrap(research)
    messages.append({"role": "user", "content": text})
    return ask_once(oai, model, messages, research=research)


def run(prompt: Optional[str] = None, research: bool = False) -> int:
    """交互 REPL。Ctrl-C → 0；配置错误 → 1。"""
    try:
        oai, model, messages = _bootstrap(research)
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
    print(f"omg chat（{mode}）· Ctrl-C 退出 · model={model}")

    first = (prompt or "").strip() or None
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
