# blogs

基于 [Hexo](https://hexo.io/) 的静态博客，推送到 `master` 后由 GitHub Actions 自动构建并发布到 GitHub Pages。

- 线上地址：https://yihuaxiang.github.io/blogs/

## 本地预览

```bash
npm install          # 首次克隆后安装依赖
npm run server       # 启动本地服务，访问 http://localhost:4000/blogs/
```

## 写文章

```bash
npx hexo new "文章标题"
# 生成 source/_posts/文章标题.md，用编辑器写正文即可
```

文章顶部的 front-matter 可设置 `title` / `date` / `tags` / `categories`。
写草稿用 `npx hexo new draft "标题"`（存在 `source/_drafts/`，默认不发布）。

## 本地构建

```bash
npm run clean        # 清理缓存与 public/
npm run build        # 生成静态文件到 public/
```

## 自动发布

推送到 `master` 分支即可，无需本地构建：

```bash
git add .
git commit -m "post: 新文章"
git push
```

GitHub Actions（`.github/workflows/pages.yml`）会自动 `npm ci` → `hexo generate` → 部署到 Pages。
也可在仓库 Actions 页面手动触发（workflow_dispatch）。

## 首次启用 Pages（只需一次）

1. 打开仓库 **Settings → Pages**。
2. **Build and deployment → Source** 选择 **GitHub Actions**。
3. 之后每次 push 到 `master` 即自动发布。

## 配置

站点设置见 `_config.yml`（标题、作者、语言等）。
本仓库发布在子路径 `/blogs/`，因此 `url: https://yihuaxiang.github.io/blogs` 且 `root: /blogs/`，请勿随意改动这两项，否则资源链接会失效。
