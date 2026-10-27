# AGENTS.md

## 日复盘：默认交付固定版式 HTML

当用户说「今日复盘 / 帮我复盘 / 复盘一下 / 今天怎么看 / 做个日复盘 / 做个复盘」时：

1. 先按 `stocks/ai/bundled_skills/omg-daily-review/SKILL.md` 取证（mood / style / rot / crowd / etf / hot / strength / list / rank / watch_advice，必要时 wencai / web_search）。
2. 最终交付物是 **HTML 文件**，不是聊天里的 Markdown 全文；Markdown 只作为内容草稿。
3. 用固定外壳渲染：

   ```sh
   python scripts/render_daily_review.py output/review_body.html \
     --title "A 股日复盘 · 数据日 YYYY-MM-DD" \
     --date YYYY-MM-DD --archive
   ```

4. `review_body.html` 是 HTML 片段，必须自带 `masthead` / `toc` / `main` / `footer`，并包含 8 个 section id：
   `conclusion` / `market` / `themes` / `rank` / `strength` / `watch` / `bottom` / `board`。
   样式与 class 参考 `stocks/templates/daily_review.html`；不要重复写 `<html>` / `<body>` / `<script>`，也不要写 `skip` / `progress` / `to-top`（外壳已提供）。
5. 归档约定：桌面 `<运行日 YYYY_MM_DD>_复盘/<数据日 YYYY_MM_DD>_复盘.html`；渲染器会处理。
6. 回复用户时给归档路径 + 1～3 句结论摘要，不要贴 Markdown 全文。
7. 若环境不能写文件：输出完整 HTML body 片段，并明确写「未归档」，不要退化成 Markdown 全文。

`.cursor/` 不是本项目日复盘流程的依赖；不要只把规则写在那里。
