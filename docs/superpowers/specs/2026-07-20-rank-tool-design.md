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
| 日复盘 skill + `SYSTEM` 工具枚举纳入 `rank` | 替代 `mood`（涨停池）/ `hot` / `rot` |
| 参数可覆盖条数（越界拒绝） | 仓位%、目标价、下单指令 |

长期产品面倾向：`omg chat`（分析）+ `omg list`（自选快照）；`rank` 作为内部取证能力存在。

## 数据与宇宙

- **现货源**：akshare 东财 A 股现货。优先 `stock_sh_a_spot_em` + `stock_sz_a_spot_em` 拼接；也可用 `stock_zh_a_spot_em` 再按宇宙过滤。不纳入北交所。
- **默认宇宙**：沪深主板 + 创业板 + 科创板；排除 ST / \*ST、北交所、无有效报价。
- **代码前缀白名单**：`600/601/603/605/000/001/002/003/300/301/688`。
- **无有效报价**：`涨跌幅` 非有限数值，或 `最新价` 非有限数值，或 `最新价 <= 0`。
- **ST**：名称含 `ST`（含 `*ST` / `S*ST` 等）即剔除。
- **排序**：按涨跌幅降序取涨幅榜、升序取跌幅榜；涨跌幅相同则按 `code` 升序稳定排序。
- **默认条数**：gainers=50，losers=20。
- **硬上限**：gainers ∈ `[1, 100]`，losers ∈ `[1, 50]`（与 `_int_arg` 一致，**越界拒绝**，不夹紧）。

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

### 标签规则（确定性）

- **industry**：用东财行业板块成分建 `code → industry` 反查表。一股多行业时取名称字典序最小者；无命中 → `null`。
- **concepts**：先取当日热点概念名有序列表（复用 `ana.concept_strength_top10` 的同一强度逻辑，取 Top20 名称作候选池）。再对入榜股做成分反查，只保留落在候选池内的概念，并按候选池顺序截取最多 3 个；无命中 → `[]`。
- 标签拉取/映射失败：榜单仍返回，对应字段置空，并计入 `meta.tag_miss`。

### 顶层 `data`

```json
{
  "as_of": "2026-07-20T15:05:01",
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

| 字段 | 定义 |
|------|------|
| `as_of` | 取数完成时刻，本地时区 `Asia/Shanghai`，格式 `YYYY-MM-DDTHH:MM:SS`（无时区后缀） |
| `meta.gainers_n` / `losers_n` | **请求**条数（参数回显） |
| `meta.scanned` | 现货接口返回的原始行数（过滤前） |
| `meta.filtered` | 通过宇宙过滤后的可排序行数 |
| `meta.tag_miss` | 返回行中 `industry is null` **且** `concepts == []` 的行数 |
| `len(gainers)` / `len(losers)` | **实际**条数（可少于请求） |

### tool 参数

- `gainers`（可选 int）：默认 50，必须 ∈ `[1, 100]`，否则 `ok=false`
- `losers`（可选 int）：默认 20，必须 ∈ `[1, 50]`，否则 `ok=false`

## 空结果与不足条数

- `filtered == 0` → `ok=false`，error 说明无可排序标的。
- 可排序标的少于请求条数 → **截断返回**，`ok=true`（实际长度 < 请求值）。
- 仅一侧为空（极端行情）但另一侧有数据 → `ok=true`。
- 两侧皆空（理论上 `filtered>0` 时不应发生；若发生）→ `ok=false`。

## 模块与接入

1. **`stocks/rank.py`**：`analyze(gainers=50, losers=20) -> dict`；失败抛异常或返回空由 tool 层转 `ok=false`（与 mood「空数据 → err」对齐：analyze 返回完整 dict，无数据时 tool 判空）。
2. **`stocks/ai/tools.py`**：注册 `rank`；吞 stdout；`{ok, data, error}`。
3. **`stocks/ai/chat.py`**：`SYSTEM` 中 tools 枚举补上 `rank`。
4. **skill**：日复盘（`.cursor` + bundled）取证维度增加 `rank`；综合规则注明「涨跌榜只摘主题归类所需代表项，勿倾倒 50+20 全表」；依据模板可含「涨跌榜」。
5. **不**修改 `main_controller.COMMANDS`。

`analyze` 成功时始终返回上述顶层结构（可能 `gainers`/`losers` 短于请求）。tool 层：若 `analyze` 抛错或 `filtered==0`（由 analyze 抛 `ValueError` / 返回约定）→ `ok=false`。

约定：`analyze` 在 `filtered==0` 或两侧皆空时 **raise ValueError**；其余截断情况正常返回 dict。

## 失败行为

- 现货拉取失败 → 异常 → tool `ok=false` + error 文案。
- `filtered==0` 或两侧皆空 → `ok=false`。
- 标签部分失败 → 榜单照常，缺标签置空，`tag_miss` 计数。
- 参数非法/越界 → **拒绝**（`ok=false`），与 `mood.days` 相同，不夹紧。

## 测试

- 单元（mock DataFrame / mock 标签映射）：宇宙过滤、ST 剔除、无有效报价、排序截断、不足条数、参数边界、`meta` 计数、concepts 按热点候选池顺序截取。
- tool：`rank` 在 schemas 中；成功路径字段齐全；越界不调用 analyze。
- skill：日复盘取证含 `rank`；bundled 与 `.cursor` 同步；`SYSTEM` 枚举含 `rank`。
- CI 不强制真实网络。

## 非目标

- 无 `omg rank` CLI 产品命令
- 无自动中文「主线解读」段落（由 chat / skill 生成）
- 不在本设计内关闭或移除其他 CLI 命令（产品收敛另议）
- 不为每只股票实时打外部个股详情接口（标签走板块成分反查）
