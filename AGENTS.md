# 本仓库写作约定（供自动化 agent 阅读）

这是一个 **Hexo** 博客，发布在 GitHub Pages 子路径 `https://yihuaxiang.github.io/blogs/`。
当被要求「写一篇新技术文章」时，严格遵守以下规则。

## 1. 选题：必须是全新、以前没写过的主题

- 先列出已有文章：读取 `source/_posts/` 下所有 `.md` 的 `title` 与文件名。
- 新文章主题**不得与任何已有文章重复或高度相似**（同一技术点换个标题也算重复）。
- 优先选择实用、长青、可自行核实正确性的工程主题（语言/工具/工程实践/性能/调试/架构等）。
- 不要写需要联网核实却无法核实的“最新资讯/版本号/价格”，避免编造事实。

## 2. 文章格式（Hexo Markdown）

- 新建文件：`source/_posts/<英文短横线 slug>.md`（slug 用英文，作为 URL）。
- front-matter 必须包含 `title`、`date`（用当前时间，格式 `YYYY-MM-DD HH:mm:ss`）、`tags`、`categories`。
- 正文开头第 1 张图作为封面，紧接一段引子后写 `<!-- more -->` 作为摘要分隔。
- 正文 800–1500 字，结构清晰（多级标题 + 代码块 + 适当表格）。

## 3. 图文并茂：用 MiniMax 生成配图

- 用脚本生成图片（环境变量 `MINIMAX_API_KEY` 已由外部注入）：
  ```bash
  python3 .codex/skills/minimax-image/scripts/minimax_image.py \
    --aspect-ratio "16:9" \
    --out source/images/<slug>/cover.jpeg \
    --prompt "Flat modern isometric vector illustration ... , clean style, no text, no letters"
  ```
- 每篇**至少 1 张封面 + 1～2 张正文插图**，全部保存在 `source/images/<slug>/` 下（按 slug 建子目录，避免覆盖）。
- 提示词用英文、扁平/等距插画风、**强调 `no text, no letters`**（AI 生图的文字会乱）。
- 生成后必须确认每个 `.jpeg` 文件存在且非空（`file` 命令应识别为 JPEG）；失败就换提示词重试，不要留下坏图。

## 4. 图片引用路径（重要，别踩坑）

- Markdown 里引用站内图片，写**相对站点根、不带 `/blogs` 前缀**的路径：
  - ✅ 正确：`![描述](/images/<slug>/cover.jpeg)`
  - ❌ 错误：`![描述](/blogs/images/...)` —— Hexo 的 marked 渲染器会自动补 `root`(`/blogs/`)，手写会变成 `/blogs/blogs/...` 导致 404。

## 5. 完成前自检

- 运行 `npx hexo clean && npx hexo generate`，确保构建无报错。
- 确认 `public/2026/.../<slug>/index.html` 里图片 `src` 是单层 `/blogs/images/...`（不是 `/blogs/blogs/`）。
- **不要执行 git commit / push**（由外部封装脚本负责提交推送）。
