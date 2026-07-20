---
name: omg-theme-research
description: >
  Use when the user asks to research a specific A-share theme, concept, industry,
  or ETF idea (e.g. 半导体、人形机器人), wants omg evidence plus optional web_search,
  or says 主题研究 / 深挖板块. Not for whole-market daily review (use omg-daily-review).
---

# omg 主题研究

## Overview

围绕**单一主题**取证：热点强度、轮动归属、拥挤度、相关 ETF、必要时外部搜索。结论仍只能来自 omg tools / 命令输出。

## 流程

1. 确认主题关键词（用户原词优先，不擅自改名）。
2. 取证（可并行）：
   - `hot`：看该主题是否出现在强度榜、近几日位置
   - `rot`：是否在回流优先 / 升温接力 / 放弃观察
   - `crowd`：同名或相近行业分位（对不上就写「无直接匹配」）
   - `etf`：主题相关 ETF 短/中线状态
   - `web_search`：仅补近 1–3 日催化/风险事件
3. 可选：`omg -k <主题>` 画强度曲线（需要出图时）。
4. 对照大环境时**轻量**调 `mood` + `style` 各一次，不要重跑完整日复盘。

## 输出

```markdown
## 主题结论
- 该主题当前更像：回流 / 升温 / 拥挤透支 / 尚未进入主线 / 数据不足

## 依据
- hot / rot / crowd / etf / search 各 1–3 句，标明来源

## 风险
- 拥挤、退潮、叙事证伪、数据缺口
```

## 禁区

- 不编造成分股涨跌
- 不给目标价与仓位
- 主题名对不上 tool 结果时明确说「未匹配」，不要硬凑
