---
name: omg-daily-review
description: >
  Use when the user asks for A-share daily review, market pulse, 今天怎么看,
  复盘, research-style market summary, or wants omg/omg chat to gather evidence
  before concluding. Prefer this over free-form market commentary.
---

# omg 日复盘（AI）

## Overview

用 `omg` 的确定性行情能力取证，再组织成简洁中文复盘。结论必须来自工具结果，禁止编造涨跌幅、情绪阶段或风格标签。

## When to Use

- 「今天怎么看」「复盘」「盘面」「research」
- 需要综合情绪 / 风格 / 轮动 / 拥挤 / ETF / 热点 / 自选
- 用户明确要基于 omg 数据，而不是泛泛宏观评论

## When NOT to Use

- 改 `stocks/` 代码、修 bug、加 tool（走工程流程，不走本 skill）
- 全市场 `filter` 筛票（成本高；本 skill 默认不做）
- 要具体买卖点、仓位比例、目标价（只给观察维度与风险）

## 执行策略（二选一）

### A. 优先：`omg chat --research`（已配置 LLM）

环境需有 `OMG_LLM_BASE_URL` / `OMG_LLM_API_KEY` / `OMG_LLM_MODEL`。

```sh
omg chat --research "按 omg 日复盘流程取证：情绪、风格、轮动、拥挤、ETF、热点、自选；必要时 web_search；先结论后依据与风险"
```

若用户给了主题（如「半导体」），把主题写进首问，并要求补 `web_search` 与相关板块对照。

非交互一次性跑完时，用 `run_once`（返回助手文本；`run()` 是交互 REPL，会继续 `input`）：

```python
from stocks.ai.chat import run_once

answer = run_once(
    "按 omg 日复盘流程取证：情绪、风格、轮动、拥挤、ETF、热点、自选；必要时 web_search；先结论后依据与风险",
    research=True,
)
print(answer)
```

把 `answer` 整理成下方「输出模板」。不要二次编造数字。

### B. 回退：Agent 直接调 tools（无 LLM 或 chat 不可用）

在仓库根目录用 Python 调薄封装，顺序固定：

```python
from stocks.ai import tools as t

order = [
    ("mood", {}),
    ("style", {}),
    ("rot", {"days": 5, "top_concepts": 8, "top_stocks": 3}),
    ("crowd", {"top": 10}),
    ("etf", {}),
    ("hot", {"days": 5, "top": 8}),
    ("list", {}),
]
results = {name: t.call(name, args) for name, args in order}
```

规则：

1. 仅使用 `ok=true` 的 `data`；`ok=false` 记入「数据缺口」，不要脑补替代。
2. 慢工具（`rot` / `crowd` / `etf`）可并行；任一失败不阻塞已成功维度。
3. 用户提到突发新闻/政策时，再调 `web_search`；日常复盘可不搜。
4. 禁止调用 `filter`。

## 取证维度（含义对齐 omg）

| 维度 | tool / 命令 | 看什么 |
|------|-------------|--------|
| 情绪 | `mood` / `omg mood` | 冰点/修复/主升/分歧/退潮 + 近 N 日 |
| 风格 | `style` / `omg style` | 科技虹吸/大小盘/红利/均衡 |
| 轮动 | `rot` / `omg rot` | 回流优先 / 升温接力 / 放弃观察 |
| 拥挤 | `crowd` / `omg crowd` | 行业成交额占比分位 |
| ETF | `etf` / `omg etf` | 主题短线/中线状态 |
| 热点 | `hot` / `omg hot` | 概念强度 Top |
| 自选 | `list` / `omg list` | 主要指数 + 自选快照 |
| 外部 | `web_search` | 仅补新闻/公告缺口 |

## 综合规则

1. **先结构后故事**：情绪 + 风格定大环境，再谈轮动/热点/ETF。
2. **冲突要写明**：例如情绪主升但风格红利防守 → 写「结构性分化」，不要强行统一。
3. **拥挤作风险阀**：高拥挤主题偏风险提示，不单独当买入理由。
4. **自选单独一小节**：与主线一致/背离各一句。
5. **缺数据就说缺**：某 tool 失败 → 「本日 XX 数据不可用」，继续其他维度。

## 输出模板（必须遵守）

用中文，短句，少形容词：

```markdown
## 结论
- 一句话定调（情绪×风格）
- 主线 1–2 条（来自 rot/hot/etf，点名依据来源）

## 依据
- 情绪：…（mood）
- 风格：…（style）
- 轮动/热点：…（rot / hot）
- 拥挤/ETF：…（crowd / etf，可略）
- 自选：…（list）
- 外部：…（仅当用了 web_search）

## 风险与观察
- 2–4 条：拥挤、退潮信号、数据缺口、需跟踪的变量
- 不给仓位/买卖点
```

## 质量闸门

交付前自检：

- [ ] 每个数字或阶段标签都能指回某次 tool / 命令输出
- [ ] 没有「建议加仓到 x%」类表述
- [ ] 失败 tool 已声明，未假装有数据
- [ ] 输出符合上方三节模板

## 示例触发

用户：`帮我复盘一下今天`

动作：检查 LLM 环境 → 有则策略 A，无则策略 B → 按模板输出。
