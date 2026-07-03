---
title: 用 Git Worktree 并行开发：少切分支，少 stash，少污染工作区
date: 2026-07-04 03:06:53
tags:
  - Git
  - 工程效率
  - 开发流程
categories:
  - 工具
---

![用 Git Worktree 并行开发](/images/git-worktree-parallel-development/cover.jpeg)

开发时最打断节奏的场景，往往不是写新功能，而是新功能写到一半，线上突然要修一个小 bug；或者正在做代码审查，本地又必须回到干净的 `main` 复现问题。反复 `stash`、切分支、装依赖，会让工作区越来越混乱。`git worktree` 的思路很朴素：同一个仓库可以挂出多个工作目录，每个目录检出不同分支，但共享同一份 Git 对象库。这样你可以保留当前现场，同时在另一个目录里处理热修复或评审。

<!-- more -->

## 它解决的不是分支，而是工作区

普通分支切换只改变一个目录里的文件状态；`worktree` 则是给同一个仓库增加多个并行目录。每个目录都有自己的工作区、暂存区和当前分支，但提交历史、对象缓存、远端信息是共享的。

| 场景 | 传统做法 | Worktree 做法 |
| --- | --- | --- |
| 功能开发中插入热修复 | `stash` 当前改动再切分支 | 新开一个 hotfix 目录 |
| 同时审查多个 PR | 来回 checkout | 每个 PR 一个目录 |
| 保留长期实验分支 | 当前目录长期不干净 | 独立目录隔离实验 |
| 复现线上问题 | 担心覆盖当前文件 | 从稳定分支挂一个干净目录 |

如果你的日常只有一个分支和少量改动，`worktree` 不一定必要。但只要你经常在“当前活没做完”和“必须马上切走”之间来回切换，它就很值得加入工具箱。

## 从一个稳定目录结构开始

![多工作区目录布局](/images/git-worktree-parallel-development/workspace-map.jpeg)

推荐把主仓库和工作区放在同级目录，命名时带上用途，方便终端、编辑器和脚本识别：

```text
projects/
  app/                 # 主工作区，长期停在 main
  app-feature-pay/     # 功能分支
  app-review-142/      # 评审某个 PR
  app-hotfix-login/    # 紧急修复
```

在主仓库里查看当前工作区：

```bash
git worktree list
```

基于已有分支挂出一个目录：

```bash
git worktree add ../app-hotfix-login hotfix/login-timeout
```

如果要同时创建新分支和新目录：

```bash
git fetch origin
git worktree add -b feature/payment-retry ../app-feature-pay origin/main
```

这个命令的意思是：以 `origin/main` 为起点创建 `feature/payment-retry`，并把它检出到 `../app-feature-pay`。主目录仍然保持原样，不需要把手头改动收起来。

## 一套可落地的日常流程

我更建议把主仓库当成“基准目录”，长期保持在 `main` 或 `master`，只做拉取、同步和少量查询。真正开发放到专门的 worktree 里：

```bash
cd ~/projects/app
git fetch origin
git pull --ff-only

git worktree add -b feature/order-export ../app-order-export origin/main
cd ../app-order-export
```

进入新目录后，该安装依赖就安装依赖，该跑测试就跑测试。注意，`node_modules`、虚拟环境、构建产物这类未跟踪文件通常是每个目录各自一份；提交历史共享，不代表运行时缓存也共享。对 Node、Python、Go 这类项目，可以依赖包管理器自己的全局缓存来减少重复下载，而不是把产物挪来挪去。

临时处理热修复时，流程也很干净：

```bash
cd ~/projects/app
git fetch origin
git worktree add -b hotfix/login-timeout ../app-hotfix-login origin/main
cd ../app-hotfix-login

# 修改、测试、提交、推送
git push -u origin hotfix/login-timeout
```

这样新功能目录里的未提交代码不会被碰到，热修复目录也天然是从最新主线开始的。

## 清理比创建更重要

![工作区清理流程](/images/git-worktree-parallel-development/cleanup-flow.jpeg)

worktree 用久了，最容易留下过期目录。完成合并后，不要直接把目录 `rm -rf` 掉，优先让 Git 解除登记：

```bash
git worktree remove ../app-hotfix-login
```

如果目录里还有未提交改动，Git 会拒绝删除，这是好事。确认真的不要以后再加 `--force`。如果你已经手工删了目录，可以回到主仓库清理失效记录：

```bash
git worktree prune
git worktree list
```

还要记住一个限制：同一个本地分支不能同时检出到两个 worktree。遇到“已经在其他工作区使用”的提示时，不要硬绕，换一个分支名更清楚，例如 `review/pr-142`、`review/pr-143`。分支名带用途，会比一堆临时目录更容易维护。

## 和团队约定几条边界

`git worktree` 是本地工具，但会影响团队协作质量。建议把约定写进项目文档或个人脚本里：

| 约定 | 原因 |
| --- | --- |
| 主目录只放主线分支 | 降低误提交、误推送概率 |
| 目录名包含任务类型 | 终端和编辑器里不容易迷路 |
| 完成后立即 remove | 避免旧分支、旧依赖长期占空间 |
| 每个目录独立跑测试 | 运行时产物不共享，不能假设状态一致 |
| 热修复从远端主线创建 | 避免带入未同步的本地历史 |

如果常用，可以封装两个别名：

```bash
git config --global alias.wtl 'worktree list'
git config --global alias.wtp 'worktree prune'
```

更进一步，可以写一个小脚本统一目录命名和创建参数，但不要把 `worktree` 包得太厚。它的价值就在于命令直观、状态可见，出了问题用 `git worktree list` 就能看懂。

## 小结

`git worktree` 不是替代分支，而是把多个分支放进多个互不污染的工作区。它特别适合功能开发、线上热修复、PR 评审和实验分支并行推进。真正用稳它，关键不在多记几个参数，而在固定目录结构、让主仓库保持干净、完成任务后及时清理。这样下次被临时需求打断时，你不用再纠结要不要 stash，直接新开一个目录就能继续工作。
