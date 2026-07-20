# omg chat tool `rank` 设计

## 目标

为 `omg chat` / skill 提供**结构化**个股涨跌幅榜取证，帮助模型解释「市场在交易什么」。  
默认返回涨幅 Top50 + 跌幅 Top20，并挂行业/概念标签。  
**不做**中文解读摘要；解读交给 LLM。

## 产品边界

| 做 | 不做 |
|----|------|
| chat tool `rank` | 正式 CLI 命令（不进 `COMMANDS`） |
| 结构化 JSON 友好载荷 | 给人看的大段话术 / 操作建议 |
| 日复盘 skill 取证维度纳入 `rank` | 替代 `mood`（涨停池）/ `hot` / `rot` |
| 参数可覆盖条数 | 仓位%、目标价、下单指令 |

长期产品面倾向：`omg chat`（分析）+ `omg list`（自选快照）；`rank` 作为内部取证能力存在。

## 数据与宇宙

- **现货源**：akshare 东财 A 股现货（沪+深拼接；与 `mood` 同属东财系，实现可选 `stock_zh_a_spot_em` 或分市场接口）。
- **默认宇宙**：沪深主板 + 创业板 + 科创板；排除 ST / \*ST、北交所、涨跌幅缺失或无有效报价。
- **代码前缀白名单**：`600/601/603/605/000/001/002/003/300/301/688`。
- **排序**：按 `涨跌幅` 降序取涨幅榜、升序取跌幅榜。
- **默认条数**：gainers=50，losers=20；上限建议 100 / 50，防止撑爆 context。

## 字段契约

### 单行

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | str | 证券代码 |
| `name` | str | 名称 |
| `change_pct` | float | 涨跌幅（%） |
| `price` | float \| null | 最新价 |
| `amount` | float \| null | 成交额 |
| `turnover` | float \| null | 换手率（%） |
| `industry` | str \| null | 至多 1 个行业；未知为 null |
| `concepts` | list[str] | 0～3 个概念；未知为 `[]` |

标签映射失败不编造；部分缺失时榜单仍返回。

### 顶层 `data`

```json
{
  "as_of": "<取数时刻或可得交易日>",
  "universe": "hs_main_cyb_kcb_ex_st",
  "gainers": [],
  "losers": [],
  "meta": {
    "gainers_n": 50,
    "losers_n": 20,
    "scanned": 0,
    "filtered": 0,
    "tag_miss": 0
  }
}
```

### tool 参数

- `gainers`（可选 int）：默认 50，上限 100
- `losers`（可选 int）：默认 20，上限 50

## 模块与接入

1. **`stocks/rank.py`**：`analyze(gainers=50, losers=20) -> dict`（纯取证；可 print 调试但不依赖 CLI）。
2. **`stocks/ai/tools.py`**：注册 handler `rank`；吞 stdout；返回 `{ok, data, error}`。
3. **skill**：`.cursor/skills/omg-daily-review/SKILL.md` 与 bundled 副本的取证维度增加 `rank`；保持双份同步测试。
4. **不**修改 `main_controller.COMMANDS`。

标签数据源在实现阶段选定（行业板成分缓存 / 概念池映射均可），**对外字段契约不变**。

## 失败行为

- 现货拉取失败或过滤后为空 → `ok=false` + 明确 `error`；不回退假数据。
- 标签部分失败 → 榜单照常，`industry`/`concepts` 置空，`meta.tag_miss` 计数。
- 参数非法或越界 → 与现有 tool（如 `mood.days`）一致：拒绝或夹紧并在 error/meta 中可观测。

## 测试

- 单元（mock DataFrame）：宇宙过滤、ST 剔除、排序截断、参数边界。
- tool：`rank` 出现在 schemas；`call("rank", …)` 成功路径字段齐全。
- skill：日复盘取证列表含 `rank`；bundled 与 `.cursor` 文案同步。
- CI 不强制真实网络。

## 非目标

- 无 `omg rank` CLI 产品命令
- 无自动中文「主线解读」段落（由 chat / skill 生成）
- 不在本设计内关闭或移除其他 CLI 命令（产品收敛另议）
