# omg

A 股行情与 AI 复盘助手：本地行情取证，大模型问答与今日复盘。

核心资产是项目内置的一组 AI skills（`stocks/ai/bundled_skills/`）：在任何通用 agent 中直接说一句自然语言，agent 按 skill 里的取证流程调用本地行情工具，输出结构化结论。

## 核心用法

### 1. 看行情

```sh
omg list
```

指数 + 自选实时快照。

### 2. 直接提问

```sh
omg chat
```

启动对话，直接问行情相关问题，结论来自本地 tools 取证。

### 3. 内置 skills：自动加载 / 手动加载

内置 skills 随项目分发在 `stocks/ai/bundled_skills/`（`omg-daily-review` 日复盘、`omg-theme-research` 主题研究）。加载方式有两种：

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

两种方式挂载后，skill 会把取证流程注入 system（按需调本地行情 tools：mood / style / rot / crowd / etf / hot / list / rank / watch_advice），并同时打开 research（更多 tool 预算）；最终输出结构化的：结论 / 依据 / 涨跌榜 / 自选操作建议 / 风险 / 建议。

## 安装

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
omg init
```

`TA-Lib` 若编译失败，需先装系统依赖。

## 配置

复制 `.env.example` 为 `.env`，填入：

```sh
OMG_LLM_BASE_URL=https://your-endpoint/v1
OMG_LLM_API_KEY=sk-...     # 必填
OMG_LLM_MODEL=your-model
```

自选文件 `stocks/my_list.txt`（已 gitignore）：`# 自选` 下每行一支股票，名称 / 代码 / 拼音缩写均可；`# 查看` 为次要观察列表。

## 数据与缓存

- 行情依赖：`adata`、`akshare`、`pandas`、`TA-Lib`
- 股票代码库等 SQLite 缓存：用户数据目录（`OMG_DATA_DIR` 可覆盖）
  - macOS: `~/Library/Application Support/omg/`
  - Linux: `${XDG_DATA_HOME:-~/.local/share}/omg/`
  - Windows: `%LOCALAPPDATA%\omg\`

## 免责声明

仅供学习、研究与个人工作流自动化，**不是**投资建议或交易指令。

## License

MIT
