"""REPL 与 tool loop。"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
1. 行情结论必须来自 tools（mood/style/rot/crowd/etf/hot/strength/list/rank/watch_advice），不要自行编造涨跌幅或阶段；分清原始数字、启发式规则标签和你的综合推断。
2. 需要外部新闻/公告时用 web_search；判断个股「该 S 还是正常回调」、以及要给低吸/关注时，必须**临时**搜最新消息面/基本面（本地只有技术面；缓存或过期资料不可用，宁可写缺口）。
3. 用中文；先结论，再依据、风险，并可给操作建议与关注建议。信息要密：名称 + 1～2 个关键数字/对比。
4. **量价**：只有 rel_vol/vol_stance、ETF 量比等带历史基准的指标能证明放量/缩量；rank/list 的绝对成交额只能说明当日规模或活跃度。无历史基准时写量能缺口。
5. 若 skill 要求「底部启动 / 推荐关注」：两节合计 ≤10 只「观察/跟踪」标的（跌侧须高门槛），**对象必须是具体票**，仍禁止买入/仓位%/目标价。
6. 控制篇幅：宁短勿水。不要大段表格或逐日罗列；只保留拐点/极值/代表项。
7. 操作/关注建议必须能回溯到刚取到的 tool 证据；用「宜/不宜/优先观察」表述。
8. 禁止：具体仓位比例、目标价、下单指令、保证收益话术。这不是投顾荐股。
9. 跨 tool 先核对 as_of/as_of_dates/trade_date/实时时点；observed_at 只是请求观察时间。rank 没有交易日期，必须标为未验证；错位或盘中数据必须标明，不能冒充同一收盘日。
10. mood/style/rot/ETF/strength 的状态与分数是描述性启发式标签，不是预测概率或已验证 alpha；证据冲突或不足时允许不下主线结论。strength 的 score 是截面分位，禁止当成上涨概率。
"""

SYSTEM_RESEARCH = """当前为 research 模式：
1. 先规划维度，再尽量多调用 tools / web_search 取证，缺口再补调；先做时点对齐，再综合，旧日与盘中数据分别标注。
2. 未启用 HTML 交付 skill 时，最终输出默认五节：## 结论 / ## 依据 / ## 风险 / ## 操作建议 / ## 关注建议；仅当 skill 要求独立涨跌榜点评时，在依据与风险之间增加 ## 涨跌榜，且勿把 rank 细节嵌进依据；若 skill 要求近期强势，在涨跌榜（若有）之后增加 ## 近期强势（strength 取证：结构 regime + 跨行业主题线 + 个股强度 + 剩余空间 space / 操作口径 action + 退潮侧，每条挂数字，归因只写同向/可能相关，action 不写成买卖建议）；若 skill 要求自选操作建议，在涨跌榜（若有）与风险之间增加 ## 自选操作建议（逐票七维：技术 / 空间与口径 / 资金 / 最新逻辑面 / 板块 / 板走势 / 大盘 + 倾向；证据来自 watch_advice，strength 覆盖时引 space/pullback/action/main_inflow）；若 skill 要求底部启动与推荐关注，放在关注建议之后：先 `## 底部启动`（strength.bottom 的启动票，按 launch 标志分排序，≤10 只），再 `## 推荐关注`（其余观察票，两节合计 ≤10 只且不重复同一只票；**对象必须是票，板块/线只能作为理由之一**；与关注建议的指标清单区分；跌侧仅高门槛且须写牵动机制）；其他 skill 仍按其模板（例如主题研究可将 rank 留在依据）。注明用过的 tool。若 skill 明确要求 HTML 交付，以 skill 的 HTML 要求为准。
3. 密度：结论 3～5 句；依据每维 2～4 句（含量价一句若有证据）；涨跌榜（若有）3～6 句主题归类；近期强势（若有）1 句 regime + 1～3 条主题线（含加速/退潮）+ 2～4 只个股（含剩余空间与低吸/追高口径）+ 退潮侧，每条挂数字；自选操作建议（若有）每票一小段且技术必含量能、逻辑面须最新核实（查不到写缺口）；风险 3～5 条；操作建议 3～5 条；关注建议 3～5 条（指标/条件，禁止个股清单）；底部启动 + 推荐关注（若有）合计 0～10 只或明示暂无。
4. 操作建议=基于证据的行为倾向（如不宜追拥挤科技、可偏向红利/防御观察、等待情绪拐点确认等），不要写成买卖点。
5. 关注建议=接下来盯什么（涨停家数、某行业拥挤分位、代表 ETF 状态、自选背离、主线量能能否续放等），尽量可验证；不要在此节列个股推荐清单。
6. 禁止空泛定调、完整多日大表、ETF 全清单、仓位%/目标价。冲突结构必须写明。
7. 主线或主题确认至少需要两类独立证据；rank 是涨跌两端截面，不能代表全市场宽度。新闻无一手确认时只写可能催化，不写确定因果。
"""

SYSTEM_HTML_REVIEW = """当前启用 omg-daily-review：最终交付必须是固定版式 HTML，不是 Markdown 全文。
**本段 HTML 要求优先于 skill 文档中的 Markdown 输出模板。**
1. 优先输出完整 HTML body 片段：自带 masthead / toc / main / footer，main 内包含 8 个 section id：conclusion / market / themes / rank / strength / watch / bottom / board。
2. 不要输出 <html> / <body> / <script>；固定外壳由 CLI 的 review_html 渲染器提供。
3. 正文完成后由 CLI 渲染并归档；不要在正文里声称“已归档”或编造文件路径。
4. 若无法输出 HTML body，则输出 Markdown 草稿，CLI 会转换；仍不要声称已归档。
5. 不要额外输出 Markdown 摘要或文件路径；CLI 会打印归档路径。
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
        tool_calls = list(msg.tool_calls)
        results: list[dict | None] = [None] * len(tool_calls)
        pending: list[tuple[int, Any, dict]] = []

        for i, tc in enumerate(tool_calls):
            if used >= limit:
                results[i] = _LIMIT_RESULT
                continue
            used += 1
            args, error = _parse_args(tc.function.arguments)
            if error:
                results[i] = {"ok": False, "data": None, "error": error}
            else:
                pending.append((i, tc, args))

        if len(pending) == 1:
            i, tc, args = pending[0]
            results[i] = ai_tools.call(tc.function.name, args)
        elif pending:
            # 同轮独立 tools 并行；消息顺序仍按 tool_calls 原序
            with ai_tools.quiet_batch():
                workers = min(len(pending), 8)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(ai_tools.call, tc.function.name, args): i
                        for i, tc, args in pending
                    }
                    for fut in as_completed(futures):
                        i = futures[fut]
                        try:
                            results[i] = fut.result()
                        except Exception as e:
                            results[i] = {
                                "ok": False,
                                "data": None,
                                "error": str(e),
                            }

        for i, tc in enumerate(tool_calls):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": ai_tools.dumps(results[i]),
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
    if skill and skill.get("name") == "omg-daily-review":
        messages.append({"role": "system", "content": SYSTEM_HTML_REVIEW})
    if skill and skill.get("body"):
        messages.append(_skill_system(skill))
    return oai, model, messages, research


def _deliver_html_review(answer: str) -> Optional[str]:
    """日复盘落成固定 HTML；短回答/闲聊不落盘。"""
    from datetime import date

    from stocks import review_html

    if not review_html.should_deliver(answer):
        return None
    try:
        target = date.today()
        title = f"A 股日复盘 · 数据日 {target:%Y-%m-%d}"
        return str(
            review_html.deliver(
                answer,
                title=title,
                data_date=target,
            )
        )
    except Exception as exc:
        print(f"（HTML 渲染失败: {exc}）", file=sys.stderr)
        return None


def run_once(
    prompt: str,
    *,
    research: bool = False,
    skill: Optional[str] = None,
) -> str:
    """单轮非交互：返回助手最终文本。

    启用 omg-daily-review 且回答足够完整时，会落成固定 HTML 并返回归档路径；
    设置 OMG_CHAT_HTML=0 可关闭该行为。
    配置错误抛 ConfigError；无效 skill 名抛 SkillError（REPL/CLI 则捕获并 exit 2）。
    """
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt 不能为空")
    loaded = ai_skills.resolve(skill, text)
    oai, model, messages, research = _bootstrap(research, skill=loaded)
    messages.append({"role": "user", "content": text})
    answer = ask_once(oai, model, messages, research=research)
    if loaded and loaded.get("name") == "omg-daily-review":
        path = _deliver_html_review(answer)
        if path:
            return f"已生成 HTML 日复盘：{path}"
    return answer


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
    failed_text: Optional[str] = None
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
            if failed_text is not None and user_text.lower() == "retry":
                user_text = failed_text
                print("（重试上一条输入）", flush=True)

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
                failed_text = user_text
                preview = " ".join(user_text.split())
                if len(preview) > 120:
                    preview = preview[:117] + "..."
                print(f"（输入 retry 可重试；已撤销：{preview}）", flush=True)
                request_failed = True
                continue
            failed_text = None
            request_failed = False
            review_path = (
                _deliver_html_review(answer)
                if loaded and loaded.get("name") == "omg-daily-review"
                else None
            )
            if review_path:
                print(f"助手> 已生成 HTML 日复盘：{review_path}\n")
            else:
                print(f"助手> {answer}\n")
    except KeyboardInterrupt:
        print()
        return 0
