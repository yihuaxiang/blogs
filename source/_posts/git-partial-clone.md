---
title: Git 部分克隆：让大型仓库按需取对象
date: 2026-11-28 03:03:28
tags:
  - Git
  - 工程实践
categories:
  - 开发工具
---

![Git 部分克隆示意图](/images/git-partial-clone/cover.jpeg)

大型仓库最慢的步骤往往不是编译，而是第一次 `clone`：历史提交、二进制资源和所有分支对象会一起传到本地。Git 部分克隆（partial clone）把“仓库元数据”和“文件内容”拆开，让客户端先拿到提交图，真正需要某个文件时再按需获取对象。

<!-- more -->

## 部分克隆解决的是什么问题

普通克隆会下载满足当前 ref 的所有可达对象。即使开发者只改一个目录，也要为很少访问的历史大文件付出网络和磁盘成本。部分克隆通过对象过滤器告诉服务端“哪些对象暂时不要发”，客户端记录一个 promisor remote，未来缺少对象时自动向它请求。

它和浅克隆不是一回事：浅克隆用深度截断提交历史，部分克隆保留完整提交图，只延迟文件对象。两者可以组合，但应分别评估历史查询和文件读取需求。

![对象过滤流示意图](/images/git-partial-clone/filter-flow.jpeg)

## 常用过滤器与命令

最常见的是 `blob:none`，它跳过所有文件内容，只下载提交、树和标签对象：

```bash
git clone --filter=blob:none --no-checkout \
  https://example.com/team/large-repo.git large-repo
cd large-repo
git sparse-checkout init --cone
git sparse-checkout set services/api
```

进入工作区后，Git 会在检出 `services/api` 时补齐所需 blob。若仓库包含大量历史二进制文件，也可以使用 `tree:0`，先不下载树对象；这种模式首次遍历目录时会产生更多按需请求，适合只执行少量固定操作的自动化任务。

过滤器 | 延迟的对象 | 适用场景
---|---|---
`blob:none` | 文件内容 | 需要完整提交历史、日常开发
`tree:0` | 文件内容和树 | CI 只读取少量已知路径
`blob:limit=1m` | 大于阈值的文件内容 | 小文件常用，大文件按需取

过滤器不是权限控制。服务端仍可能允许客户端请求被延迟的对象，因此敏感文件必须依靠仓库权限和服务端策略保护。

## 使用时的三个边界

第一，网络请求会从 clone 阶段转移到运行阶段。`git log` 通常不需要 blob，但 `git grep`、切换到旧提交、生成归档文件都可能触发 fetch。离线构建要先执行一次完整预取，或在缓存层保留对象：

```bash
git fetch --filter=blob:none origin main
git rev-list --objects --all --missing=print
```

第二，工具链可能假设对象本地存在。某些脚本直接读取 `.git/objects`，或者用文件系统扫描代替 Git 命令，遇到缺失 blob 就会失败。应在 CI 中把“按需获取”纳入网络白名单，并给 fetch 设置超时。

第三，服务端必须支持协议 v2 和过滤能力。可以先检查协商结果，再决定是否启用：

```bash
GIT_TRACE=1 GIT_TRACE_PACKET=1 \
  git -c protocol.version=2 ls-remote https://example.com/team/large-repo.git
```

## 在 CI 中落地

![CI 按需检出示意图](/images/git-partial-clone/ci-workflow.jpeg)

CI 的目标通常是缩短冷启动，而不是让每个任务都最省磁盘。建议把仓库对象缓存到持久化目录，使用固定过滤器和显式 sparse-checkout 路径；任务开始时记录缺失对象请求数量，结束时清理工作树而保留对象缓存。构建失败时，把 Git fetch 日志和实际访问路径一起输出，否则“编译失败”很容易被误判成代码问题。

上线前可以用一张小清单验证：

1. 对比普通 clone 与部分克隆的下载字节数和首个可用时间。
2. 执行完整测试、生成版本归档，确认没有隐式离线依赖。
3. 模拟远端暂时不可用，确认缓存命中时任务行为明确。
4. 监控 promisor 请求的延迟和失败率，必要时为关键 job 预取对象。

部分克隆最适合“历史很大、工作集很小、网络可控”的仓库。它不是单独的加速开关，而是把对象获取时机重新安排；只有同时设计缓存、网络和工具兼容性，收益才会稳定。
