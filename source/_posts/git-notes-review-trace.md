---
title: 用 Git Notes 给提交附加评审与诊断信息
date: 2026-11-10 03:02:23
tags:
   - Git
   - 工程实践
   - 代码评审
categories:
   - 开发工具
---

![Git Notes 提交注释封面](/images/git-notes-review-trace/cover.jpeg)

提交对象适合保存“代码改了什么”，却不适合承载临时评审结论、线上诊断链接和自动化扫描结果。把这些内容写进提交消息会污染历史，放在 issue 里又容易和具体提交脱节。Git Notes 提供了一个独立的注释对象，可以按提交关联额外信息，同时不改变提交哈希。

<!-- more -->

## Git Notes 到底存在哪里

一次提交由树、父提交、作者、提交者和消息等字段计算出哈希。`git notes` 不会修改这些字段，而是创建一棵特殊引用下的树，默认引用名为 `refs/notes/commits`。因此，同一个提交可以在不重新提交的情况下增加或修改注释。

```bash
git notes add -m "review: 通过；风险：需要观察缓存命中率" HEAD
git log --show-notes=commits -1
git notes show HEAD
git notes edit HEAD
```

注释属于仓库对象的一部分，但不会随着普通分支自动推送。查看本地提交时，是否显示注释还取决于日志参数和配置。可以把它理解成“提交旁边的一条可版本化侧车数据”，而不是提交消息的第二段。

![Git Notes 对象结构示意](/images/git-notes-review-trace/note-object.jpeg)

## 适合记录哪些内容

Notes 最适合机器或人都需要追溯、但不应该影响代码身份的信息。常见场景如下：

| 场景 | 注释内容 | 价值 |
| --- | --- | --- |
| 代码评审 | 评审结论、风险项、复查人 | 离开平台后仍能随提交查看 |
| 发布记录 | 灰度批次、回滚点、监控链接 | 复盘时快速还原上下文 |
| 安全扫描 | 扫描器版本、结果摘要、豁免原因 | 不改动业务提交，保留审计线索 |
| 性能实验 | 基准数据、测试环境、样本范围 | 避免把一次性实验写进产品代码 |

注释不应替代需求、缺陷或正式变更记录。它更像提交的索引和现场卡片：内容要短，链接要稳定，结论要能被重新验证。

## 让团队共享 Notes

默认的 `git push` 不会带上 Notes，需要明确推送引用：

```bash
git push origin refs/notes/commits

git fetch origin refs/notes/commits:refs/notes/commits
```

团队可以把这段规则写进仓库文档，或者配置远程的默认 refspec。第一次拉取时建议使用显式命令，避免把同名本地注释误认为远程最新状态。多人同时编辑注释时，Notes 也会像普通提交一样产生分叉，需要先拉取再合并。

```bash
git fetch origin refs/notes/commits:refs/notes/commits
git notes merge refs/notes/commits
```

不同用途可以使用不同 notes ref，例如 `refs/notes/review` 和 `refs/notes/ci`，分别由评审和流水线维护。查看时通过 `--notes=review` 指定来源，避免自动化任务覆盖人工结论。

![团队共享 Git Notes 的流程](/images/git-notes-review-trace/team-workflow.jpeg)

## 常见坑与治理建议

第一，注释不是提交签名的一部分。有人修改 Notes 后，提交本身的签名仍然有效，但注释内容需要独立的权限和审计策略。第二，浅克隆和只拉取单个分支的流水线可能没有 Notes 引用，脚本必须把“注释缺失”视为可处理状态。第三，注释内容也可能泄露内部地址、用户数据或密钥，写入前应做脱敏。

一个实用的约定是：人工评审只写结论、风险和后续链接；CI 只写检查批次与摘要；每条注释首行使用固定前缀，后续内容使用 Markdown。这样既方便 `git notes show` 阅读，也便于脚本按前缀检索。

## 一套可落地的最小流程

1. 提交完成后，用 `git notes add` 记录评审或发布编号。
2. 在 CI 中显式 fetch Notes，并把缺失视为警告而非构建失败。
3. 推送代码时同时推送 `refs/notes/commits`。
4. 每月清理失效链接和敏感内容，必要时把重要结论迁移到正式系统。

Git Notes 的价值不在于替代协作平台，而在于把“这次提交后来发生了什么”附着在提交坐标上。只要明确 ref 的所有者、同步方式和内容边界，它就能成为低成本、可追溯的工程旁路记录。
