---
title: 用 Git Bisect 二分定位回归：把“哪次改坏了”交给自动测试
date: 2026-07-26 03:04:27
tags:
  - Git
  - 调试
  - 自动化测试
  - 故障排查
categories:
  - 工程实践
---

![Git Bisect 在提交历史中定位回归](/images/git-bisect-regression-debugging/cover.jpeg)

功能昨天还正常，今天却悄悄坏了。面对几十甚至几百个候选提交，逐个回退既慢又容易漏掉环境差异。`git bisect` 会在一段提交历史上执行二分查找；只要能稳定判断某个提交是“好”还是“坏”，就能用很少的检出次数逼近第一个引入问题的提交。

<!-- more -->

## 先把现象变成判定条件

二分定位的前提不是“怀疑某行代码”，而是存在两个边界：当前坏提交与一个已知正常的旧提交。开始前先保存未提交改动，并确认测试在同一依赖、配置和数据条件下可重复运行。偶发失败会向 Git 提供错误方向，最终得到看似精确却不可信的答案。

| 判定结果 | 含义 | 自动脚本退出码 |
| --- | --- | --- |
| good | 该提交没有目标回归 | `0` |
| bad | 该提交能复现目标回归 | `1`–`127`（除 `125`） |
| skip | 该提交无法构建或无法判定 | `125` |

例如，已知当前分支有问题，而发布标签 `v1.4.0` 正常：

```bash
git status --short
git bisect start
git bisect bad HEAD
git bisect good v1.4.0
```

Git 随即检出中间提交。此时运行最小复现测试，根据结果执行 `git bisect good` 或 `git bisect bad`。每次判断都会把候选范围缩小约一半；一百个候选提交通常只需约七次可靠判断。

![二分查找逐步缩小可疑提交范围](/images/git-bisect-regression-debugging/search-path.jpeg)

## 让脚本代替人工判断

最有效的做法是把复现步骤封装成脚本，再交给 `git bisect run`。脚本只判断目标故障，不要顺手运行整个测试套件，否则无关失败也可能被误判为回归。

```bash
#!/usr/bin/env bash
set -u

npm ci --silent || exit 125
npm test -- --runInBand tests/order-total.test.js
status=$?

if [ "$status" -eq 0 ]; then
  exit 0
fi
exit 1
```

```bash
git bisect run ./scripts/check-order-regression.sh
```

这里把安装失败映射为 `125`，表示该提交不可测试，而不是断言它包含目标缺陷。测试失败则返回 `1`。若要查找“某项能力首次出现”的提交，也可以反转判定逻辑，但必须保证传给 Git 的 `good` 与 `bad` 始终表达同一语义。

## 遇到不可构建提交怎么办

历史中的中间提交可能缺少迁移文件、依赖已经下线，或只能在旧工具链下构建。可以执行 `git bisect skip` 跳过当前点；自动脚本则返回 `125`。Git 会换一个邻近候选继续搜索。

跳过过多连续提交时，结果可能变成一个小范围而非唯一提交。这不是工具失效，而是证据不足。此时应优先修复测试环境、为旧提交准备兼容容器，或在剩余范围内审查差异：

```bash
git log --oneline --decorate good_commit..bad_commit
git diff good_commit..bad_commit -- src/orders tests
```

合并历史还会带来另一种陷阱：被检出的提交可能是合并点，真正的问题来自某条侧分支。先看 `git show --first-parent <commit>` 理解主线变化，再查看合并提交的父节点与相关分支，不要只盯着最终补丁。

## 保留证据并恢复工作区

定位完成后，记录首个坏提交、好坏边界、测试脚本、运行环境和失败输出。随后退出二分状态：

```bash
git bisect log > bisect-session.log
git bisect reset
```

`git bisect reset` 会回到开始前的分支或提交；它不会自动修复缺陷。修复时应先补一条能在坏提交失败、修复后通过的回归测试，再修改实现。若想复查判断过程，可用 `git bisect replay bisect-session.log` 重放记录。

`git bisect` 的价值不只是节省检出次数，它迫使排障过程拥有明确边界和机器可执行的判据。真正决定结果质量的，是稳定的最小复现、正确的退出码，以及对不可测历史的诚实处理。把这些证据一并保留下来，“谁改坏了”的争论就会变成一次可复核的工程实验。
