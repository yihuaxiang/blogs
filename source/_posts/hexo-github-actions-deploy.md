---
title: 用 GitHub Actions 自动部署 Hexo 博客（含 /blogs 子路径踩坑）
date: 2026-06-16 15:00:00
tags:
  - Hexo
  - GitHub Actions
  - GitHub Pages
  - CI/CD
categories:
  - 建站
---

![用 GitHub Actions 自动部署 Hexo 博客](/images/hexo-github-actions-deploy/cover.jpeg)

搭一个 Hexo 博客不难，难的是「每次写完还要本地 `hexo generate` 再手动推一堆静态文件」。把构建交给 GitHub Actions 后，工作流就变成：**写文章 → `git push` → 几十秒后自动上线**，本地连 `node_modules` 都不用装。这篇记录一套能直接抄的配置，以及我自己踩到的几个坑。

<!-- more -->

## 为什么用 Actions，而不是本地 `hexo deploy`

传统做法是本地构建好，再用 `hexo-deployer-git` 把 `public/` 推到 `gh-pages` 分支。问题是：

- 换台电脑就得重新装环境；
- 构建产物进了 git 历史，仓库越来越臃肿；
- 忘了构建直接推源码，线上就不更新。

交给 Actions 后，仓库里**只放源码**，构建在云端干净环境里跑，产物直接交给 GitHub Pages，不落进 git 历史。这是目前的主流做法。

## 一份能直接用的工作流

在仓库根目录建 `.github/workflows/pages.yml`：

```yaml
name: Deploy Hexo site to Pages

on:
  push:
    branches:
      - master
  workflow_dispatch:        # 允许在 Actions 页面手动触发

permissions:                # 授予 GITHUB_TOKEN 部署 Pages 的权限
  contents: read
  pages: write
  id-token: write

concurrency:                # 同一时间只跑一个部署
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
      - run: npm ci
      - run: npx hexo generate
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: ./public

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

关键点：

- 用官方的 `configure-pages` / `upload-pages-artifact` / `deploy-pages` 三件套，不用自己折腾 `gh-pages` 分支。
- `permissions` 三行必须有，否则 `deploy-pages` 没权限。
- `build` 和 `deploy` 拆成两个 job，部署单独跑、状态更清晰。

## 坑一：项目站的子路径

如果你的仓库不是 `用户名.github.io`，而是普通仓库（比如 `blogs`），站点会发布在**子路径** `https://用户名.github.io/blogs/` 下。这时 `_config.yml` 必须同时设对两项：

```yaml
url: https://用户名.github.io/blogs
root: /blogs/
```

`root` 没设的话，CSS、JS、图片全会去根路径找，结果就是「页面能打开但样式全丢」。设好后构建产物里的链接会变成 `/blogs/css/style.css`，本地 `hexo server` 也会跑在 `localhost:4000/blogs/`。

## 坑二：`npm ci` 需要 lockfile

工作流里用了 `npm ci`（比 `npm install` 更快、更可复现），但它**强依赖 `package-lock.json`**。Hexo 脚手架默认可能给的是 `yarn.lock`，于是 Actions 直接报错。本地补一条即可：

```bash
npm install --package-lock-only   # 只生成 package-lock.json
```

把 `package-lock.json` 提交进仓库，`cache: npm` 的缓存也才能命中。

## 坑三：第一次必须手动开 Pages 开关

工作流写好推上去，第一次大概率还是失败，错误卡在 **Setup Pages** 这一步。原因是仓库还没启用 Pages：

1. 打开仓库 **Settings → Pages**；
2. **Build and deployment → Source** 选 **GitHub Actions**；
3. 回 Actions 页面 **Re-run jobs** 重跑。

这一步只需做一次，之后每次 push 就全自动了。

## 坑四：GitHub 偷偷塞进来的 Jekyll 工作流

这个最隐蔽。当你在网页上启用 Pages 时，GitHub 有时会**自动帮你提交一个 Jekyll 工作流**（`jekyll-gh-pages.yml`）。于是仓库里同时有两个工作流都监听 `push` 且都往 Pages 部署：

- 你的 Hexo 工作流——正常构建；
- 自动塞进来的 Jekyll 工作流——把 Hexo 源码当 Jekyll 编译，必然失败，还和 Hexo 抢同一个 `github-pages` 部署。

表现就是：明明 Hexo 构建成功了，线上内容却时好时坏、或者总有一条红叉。解决很简单——删掉那个 Jekyll 工作流：

```bash
git rm .github/workflows/jekyll-gh-pages.yml
git commit -m "ci: 移除自动生成的 Jekyll 工作流"
git push
```

## 日常发文流程

配好之后，写博客就只剩三步：

```bash
npx hexo new "文章标题"      # 生成 source/_posts/文章标题.md
# ……编辑正文……
git add . && git commit -m "post: 新文章" && git push
```

剩下的构建和发布，交给 Actions 就行。

## 小结

- 用官方 Pages Actions 三件套，源码归源码、产物归 Pages；
- 项目站记得 `url` + `root` 一起设子路径；
- `npm ci` 配 `package-lock.json`；
- 第一次手动把 Pages 的 Source 设成 GitHub Actions；
- 留意并删掉 GitHub 自动塞的 Jekyll 工作流。

这套跑通之后，博客就真正变成「写完即发」了。
