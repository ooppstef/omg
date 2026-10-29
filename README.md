# omg

A 股行情与 AI 复盘助手：本地行情取证，大模型问答与今日复盘。

核心资产是项目内置的一组 AI skills（`stocks/ai/bundled_skills/`）：在支持读取 SKILL.md 的 agent 中，直接说一句自然语言，agent 按 skill 里的取证流程调用本地行情工具，输出结构化结论。

## 核心用法

### 1. 看行情

```sh
omg list
```

指数 + 自选实时快照。

### 2. 直接提问

```sh
omg chat                    # 交互式对话
omg chat "半导体今天强不强"  # 带首问，问完继续对话
omg chat --research         # research 模式：提高 tool 预算，多步取证
```

直接问行情相关问题，结论来自本地 tools 取证。

### 3. 题材库与问财

```sh
omg theme                 # 东财题材库：今日机会 / 近期热点 / 热门主题 / 个股风向标
omg wencai "今日涨停"      # 问财自然语言查询（同花顺），如 概念成分 / 连板梯队
```

问财默认匿名访问；若触发限流或登录墙，可在 `.env` 配置 `OMG_WENCAI_COOKIE`（浏览器登录后复制 Cookie）兜底。

### 4. 内置 skills：自动加载 / 手动加载

内置 skills 随项目分发在 `stocks/ai/bundled_skills/`（`omg-daily-review` 日复盘、`omg-theme-research` 主题研究）。若存在 `.cursor/skills/*/SKILL.md`，会优先加载用于本地覆盖；它不是项目运行的必要依赖。加载方式有两种：

**自动加载**：首问命中关键词即自动注入对应 skill，无需指定。

```sh
omg chat "帮我复盘一下今天"        # 命中「帮我复盘」→ omg-daily-review
omg chat "深挖一下板块 半导体"     # 命中「深挖板块」→ omg-theme-research
```

触发词见各 skill 的 frontmatter description（如「复盘一下 / 今日复盘 / 做个复盘 / 今天怎么看」；裸词「复盘」不触发）。

**手动加载**：显式指定，优先级高于自动匹配；不再做关键词判断。

```sh
omg chat --list-skills                 # 查看可用 skills
omg chat --skill omg-daily-review      # 强制挂载日复盘
omg chat --skill omg-theme-research    # 强制挂载主题研究
```

两种方式挂载后，skill 会把取证流程注入 system（按需调本地行情 tools：mood / style / rot / crowd / etf / hot / strength / leaders / list / rank / theme / wencai / watch_advice），并同时打开 research（更多 tool 预算）。日复盘的内容模板为：结论 / 依据 / 涨跌榜 / 近期强势 / 自选操作建议 / 风险 / 操作建议 / 关注建议 / 底部启动 / 推荐关注。其中 `leaders` 专门做 S 点：取近 10 日涨幅前 N 的题材龙头，用当日开/高/低/收算最大涨幅、收盘涨幅、回落 pp、触板开板，输出「未触发 / S点预警 / S点确认（分时）」，补收盘宽度看不到的龙头负反馈。

### 5. 日复盘 HTML（默认交付）

日复盘默认交付固定版式 HTML，不再把 Markdown 全文当最终结果。HTML 外壳与样式在 `stocks/templates/daily_review.html`，渲染器：

```sh
python scripts/render_daily_review.py output/review_body.html \
  --title "A 股日复盘 · 数据日 2026-10-27" \
  --date 2026-10-27 --archive --open
```

`review_body.html` 是 HTML 片段，需包含 8 个 section：结论 / 盘面仪表 / 主线与轮动 / 涨跌榜 / 近期强势 / 自选操作建议 / 底部启动与推荐关注 / 风险与动作。渲染器会自动补全 CSS/JS 外壳，并按 `<运行日>_复盘/<数据日>_复盘.html` 归档到桌面。`omg chat` 启用日复盘 skill 时也会在回答完成后自动落成 HTML；设置 `OMG_CHAT_HTML=0` 可关闭。

## 其他命令

除上面的主路径外，还保留这些传统 CLI 命令：

| 命令 | 说明 |
| --- | --- |
| `omg init` | 初始化自选文件与股票基础数据（安装后执行一次） |
| `omg hot` | 概念强度 Top10（近 10 日超额收益与成交额加权） |
| `omg rot` | 近 5 日板块轮动：回流优先 / 升温接力 / 放弃观察 |
| `omg crowd` | 行业交易拥挤度（3 年成交额分位） |
| `omg etf` | 主题 ETF 短线 / 中线状态与量价点评 |
| `omg mood` | 涨停四池的市场情绪阶段 |
| `omg style` | 大小盘 / 科技 / 红利风格结构 |
| `omg notice` | `# 查看` 列表的实时行情 |
| `omg -k 半导体 人工智能` | 概念板块强度曲线，支持多个板块对比 |
| `omg filter` | 全市场日线筛选（交互式，当前策略：趋势回调企稳） |
| `omg copy` | 把 `# 自选` 同步到 shell 环境变量 `OMG_COPY_TARGET_PATH` 指定的文件 |

`omg filter` 首次运行会拉取全市场约 200 个交易日的日线并落库，耗时较长；之后按交易日增量更新，日线缓存在 `stocks.db`（见「数据与缓存」），体积可能达到数百 MB。运行后按菜单提示选择策略（当前仅 1 个），按 ESC 退出。

## 安装

Python 3.10–3.13。

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
omg init
```

`omg init` 会在 `stocks/my_list.txt` 不存在时从示例生成，并下载股票名称 / 代码索引。

`TA-Lib` 若编译失败，需先装系统依赖。

## 配置

复制 `.env.example` 为 `.env`，填入：

```sh
OMG_LLM_BASE_URL=https://your-endpoint/v1
OMG_LLM_API_KEY=sk-...     # 必填
OMG_LLM_MODEL=your-model
OMG_LLM_TIMEOUT=120        # 可选，秒，1..600；不填默认 60
```

其他可选配置（shell 环境变量；`omg chat` 也会读取 `.env` 中的同名项）：`OMG_SEARCH_MAX_RESULTS`（web_search 条数，默认 5，1..20）、`OMG_MOOD_DAYS` / `OMG_STYLE_DAYS`（趋势天数，默认 8）、`OMG_ETF_MAX`（只扫前 N 只 ETF，便于快速试跑）、`OMG_CHAT_HTML=0`（关闭 `omg chat` 日复盘自动落 HTML）。`OMG_DISABLE_DOTENV=1` 只能通过 shell 环境变量设置，用于跳过 `.env`。

除 `omg chat` 外，其他命令不需要配置大模型。

自选文件 `stocks/my_list.txt`（已 gitignore）：`# 自选` 下每行一支股票，名称 / 代码 / 拼音缩写均可；`# 查看` 为 `omg notice` 读取的次要观察列表。

## 数据与缓存

- 行情依赖：`adata`、`akshare`、`pandas`、`TA-Lib`；问财 token 生成依赖 `py-mini-racer`
- 股票基础数据、全市场日线（`omg filter` 使用）与更新标记等缓存位于用户数据目录（shell 环境变量 `OMG_DATA_DIR` 可覆盖，必须是绝对路径）；其中 `stocks.db` 为 SQLite
  - macOS: `~/Library/Application Support/omg/`
  - Linux: `${XDG_DATA_HOME:-~/.local/share}/omg/`
  - Windows: `%LOCALAPPDATA%\omg\`

## 免责声明

仅供学习、研究与个人工作流自动化，**不是**投资建议或交易指令。

## License

MIT
