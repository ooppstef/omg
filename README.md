# omg

A 股 **AI 日复盘** 工具：用本地行情 tools 取证，再由大模型写成结构化复盘（结论 / 依据 / 涨跌榜 / 自选操作建议 / 风险 / 建议）。

日常主路径只有：配置 API → 维护自选 → `omg chat` 复盘。其余 CLI 命令仍可用，但不是推荐用法。

## 你能得到什么

一次日复盘大致覆盖：

- 情绪 × 风格定调（mood / style）
- 主线与轮动、热点、拥挤、ETF（rot / hot / crowd / etf）
- 涨跌榜主题归类（rank）
- **自选逐票**：技术面 + 板块 + 板走势 + 大盘锚 → 宜/不宜/优先观察（watch_advice）

不给仓位比例、目标价、下单指令。

## 快速开始

Python 3.10–3.13。

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
omg init
```

`TA-Lib` 若编译失败，需先装系统依赖。

### 1. 配置大模型（`omg chat` 必需）

```sh
cp .env.example .env
```

编辑 `.env`（已 gitignore，勿提交）：

```sh
OMG_LLM_BASE_URL=https://your-endpoint/v1
OMG_LLM_API_KEY=sk-...     # 必填，否则 chat 无法启动
OMG_LLM_MODEL=your-model
OMG_LLM_TIMEOUT=120        # 可选，1..600
```

也可用 `export` 同名环境变量；已有环境变量优先于 `.env`。

### 2. 维护自选

自选在仓库内（私人文件，已 gitignore）：

```sh
cp stocks/my_list.txt.example stocks/my_list.txt
# 编辑 stocks/my_list.txt
```

```text
# 自选
华虹公司
600000

# 查看
```

- `# 自选`：日复盘 / `watch_advice` / `omg list` 使用  
- `# 查看`：次要观察列表（`omg notice`）

若以前用过 macOS  
`~/Library/Application Support/omg/my_list.txt`，请把内容拷进 `stocks/my_list.txt`——旧路径不再读取。

### 3. 开始复盘

```sh
omg chat "帮我复盘一下今天"
```

会自动挂载日复盘 skill，并打开 research（更多 tool 预算）。也可：

```sh
omg chat --list-skills
omg chat --skill omg-daily-review
omg chat "深挖一下板块 半导体"    # 主题研究 skill
omg list                         # 仅看指数 + 自选行情
```

退出：`Ctrl-C` 或输入 `exit` / `quit`。

## 复盘时模型会调哪些 tools

| Tool | 用途 |
|------|------|
| mood / style | 情绪阶段、大小盘/科技/红利风格 |
| rot / hot / crowd / etf | 轮动、概念强度、行业拥挤、主题 ETF |
| rank | 涨幅/跌幅榜 + 行业与热点概念 |
| watch_advice | 自选逐票技术 + 板块对齐 + 大盘锚 |
| list | 指数与自选实时快照 |
| web_search | 突发新闻/公告（按需） |

证据来自 tools；禁止编造涨跌与阶段。

## 其它 CLI（非重点）

仍保留 `hot` / `rot` / `crowd` / `etf` / `mood` / `style` / `filter` / `copy` / `-k` 等，便于单独拉数或调试。  
产品重心是 **`omg chat` 复盘**，不必先学完整命令表。

## 数据与缓存

- 行情依赖：`adata`、`akshare`、`pandas`、`TA-Lib` 等（见 `requirements.txt`）
- 自选文本：`stocks/my_list.txt`
- 股票代码库等 SQLite 缓存：用户数据目录（可用 `OMG_DATA_DIR` 覆盖）
  - macOS: `~/Library/Application Support/omg/`
  - Linux: `${XDG_DATA_HOME:-~/.local/share}/omg/`
  - Windows: `%LOCALAPPDATA%\omg\`

## 状态

个人工作流工具，面向开源整理中；接口与输出格式可能调整。

## 免责声明

仅供学习、研究与个人工作流自动化，**不是**投资建议或交易指令。请自行核实行情并独立决策。

## License

MIT
