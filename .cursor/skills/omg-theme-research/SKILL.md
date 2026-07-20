---
name: omg-theme-research
description: >
  Use when the user asks to research a specific A-share theme, concept, industry,
  or ETF idea (e.g. 半导体、人形机器人), wants omg evidence plus optional web_search,
  or says 主题研究 / 板块研究 / 深挖板块 / 深挖一下板块 /
  深挖一下主题 / 深挖一下这个主题.
  Not for whole-market daily review (use omg-daily-review).
---

# omg 主题研究

## Overview

围绕**单一主题**取证：热点强度、轮动归属、拥挤度、相关 ETF、必要时外部搜索。结论仍只能来自 omg tools / 命令输出。

## When to Use

- 「主题研究」「板块研究」「深挖板块」「深挖一下板块」「深挖一下主题」「深挖一下这个主题」
- 围绕单一概念/行业/主题取证，而不是全市场日复盘

自动挂载或显式 `--skill omg-theme-research` 时也会打开 research。
裸词「深挖 / 盘面 / research」不会触发；「板块研究员」「深挖一下主题性」等复合说法也不会。

```sh
omg chat --skill omg-theme-research
omg chat "深挖一下板块 半导体"
```

## 流程

1. 确认主题关键词（用户原词优先，不擅自改名）。
2. 取证（可并行）：
   - `hot`：看该主题是否出现在强度榜、近几日位置
   - `rot`：是否在回流优先 / 升温接力 / 放弃观察
   - `crowd`：同名或相近行业分位（对不上就写「无直接匹配」）
   - `etf`：主题相关 ETF 短/中线状态
   - `rank`：涨跌幅榜里该主题/行业是否成簇出现（代表股+涨跌幅；勿倾倒全表）
   - `web_search`：仅补近 1–3 日催化/风险事件
3. 对照大环境时**轻量**调 `mood` + `style` 各一次，不要重跑完整日复盘。
4. 需要强度曲线图时，由用户在终端自行 `omg -k <主题>`（chat 内不要跑 CLI）。

## 输出

信息要密、篇幅中等。可给操作/关注建议，但挂靠证据。

```markdown
## 结论
- 该主题当前更像：回流 / 升温 / 拥挤透支 / 尚未进入主线 / 数据不足（带 1 个关键数）

## 依据
- hot / rot / crowd / etf / rank / search：各 1～3 句，标明来源与数字

## 风险
- 拥挤、退潮、叙事证伪、数据缺口（可验证）

## 操作建议
- 3～5 条宜/不宜/优先观察（禁仓位%/目标价/下单指令）

## 关注建议
- 3～5 条接下来盯的信号或标的维度
```

## 禁区

- 不编造成分股涨跌
- 不给目标价、仓位比例、下单指令
- 主题名对不上 tool 结果时明确说「未匹配」，不要硬凑
