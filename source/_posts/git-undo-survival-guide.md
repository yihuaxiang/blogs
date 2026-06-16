---
title: Git「后悔药」救命指南：改错了、删没了、提交歪了怎么办
date: 2026-06-16 15:30:00
tags:
  - Git
  - 版本控制
  - 效率
categories:
  - 工具
---

![Git 后悔药救命指南](/images/git-undo-survival-guide/cover.jpeg)

用 Git 最慌的时刻，往往不是写代码，而是「我刚才那一下是不是把东西搞没了」。好消息是：**只要你曾经 `git add` 或 `git commit` 过，几乎没有真正找不回来的东西。** 这篇按「我闯了什么祸」来组织，遇到对应场景直接抄命令。

<!-- more -->

## 改乱了工作区，想恢复到上次提交

还没 `add`、只是文件改花了想丢弃：

```bash
git restore 文件名          # 丢弃单个文件的改动
git restore .               # 丢弃所有未暂存的改动
```

已经 `git add` 进暂存区，想撤回暂存（但保留改动）：

```bash
git restore --staged 文件名   # 从暂存区拿出来，改动还在
```

> `git restore` 是较新的命令，语义比老的 `git checkout -- 文件` 更清晰，推荐用它。

## 提交信息写错了 / 漏提交了一个文件

最近一次提交还没推出去，想改：

```bash
git commit --amend                 # 修改最近一次提交信息
git add 漏掉的文件 && git commit --amend --no-edit   # 把文件补进上一次提交，不改信息
```

注意：`--amend` 会**改写历史**，如果这次提交已经 push 过，再 amend 就要 `git push --force-with-lease`，且只在自己的分支上做。

## 想撤销提交，但分清三种 reset

`git reset` 是最容易用错的命令，关键在于它怎么处理你的改动：

```bash
git reset --soft HEAD~1    # 撤销提交，改动留在“暂存区”
git reset --mixed HEAD~1   # （默认）撤销提交，改动留在“工作区”
git reset --hard HEAD~1    # 撤销提交，并丢弃改动 —— 危险！
```

记忆口诀：

- `--soft`：只退提交，东西还在台面上（暂存区），改个提交粒度常用它。
- `--mixed`：退提交 + 退暂存，代码还在但要重新 `add`。
- `--hard`：连代码一起抹掉，**用前先想清楚**。

## 已经推到远端的提交，要安全撤销

如果这次提交别人可能已经拉走了，**不要 reset + 强推**，而是用 `revert` 生成一个「反向提交」：

```bash
git revert <commit>        # 新增一次提交来抵消指定提交，历史不被改写
```

这样协作者拉下来不会冲突，是公共分支上撤销的正确姿势。

## 最强救命稻草：`git reflog`

不管你怎么 `reset --hard`、误删分支、rebase 翻车，只要操作发生在本地，**HEAD 的每一次移动都被记在 reflog 里**：

```bash
git reflog                 # 看 HEAD 走过的每一步，带 commit hash
git reset --hard abc1234   # 跳回任意一个历史状态
```

举个典型场景——`reset --hard` 之后发现删多了：

```bash
git reflog                 # 找到 reset 之前那条记录，比如 HEAD@{1}
git reset --hard HEAD@{1}  # 满血复活
```

误删的分支也能这样找回：在 reflog 里找到分支最后所在的 commit，再 `git branch 分支名 <hash>` 重新建出来。

## 手头活没干完，但要临时切分支

用 `stash` 把改动暂存起来，工作区瞬间变干净：

```bash
git stash                  # 收起当前所有改动
git stash -u               # 连未跟踪的新文件一起收
git stash list             # 看存了哪些
git stash pop              # 取回最近一次并从栈里移除
git stash apply stash@{1}  # 取回指定的某次（保留在栈里）
```

## 只想要别的分支上的某一个提交

不需要整条分支合过来，只挑一个提交：

```bash
git cherry-pick <commit>          # 把某个提交“搬”到当前分支
git cherry-pick <a>^..<b>         # 搬一段范围
```

常用于「热修复在 main 上，要同步一份到发布分支」这种场景。

## 一张速查表

| 我想… | 命令 |
| --- | --- |
| 丢弃工作区改动 | `git restore .` |
| 取消暂存 | `git restore --staged 文件` |
| 改最近提交信息 | `git commit --amend` |
| 退掉提交但留代码 | `git reset --soft/--mixed HEAD~1` |
| 退掉提交并删代码 | `git reset --hard HEAD~1`（慎用） |
| 撤销已推送的提交 | `git revert <commit>` |
| 找回任何「丢失」的状态 | `git reflog` + `git reset --hard` |
| 临时收起改动 | `git stash` / `git stash pop` |
| 摘一个提交过来 | `git cherry-pick <commit>` |

## 小结

记住两条就不会慌：**`--hard` 和 `--force` 之前先停一秒**，以及**真出事了先 `git reflog`**。Git 的安全网比想象中结实，绝大多数「完蛋了」其实都救得回来。
