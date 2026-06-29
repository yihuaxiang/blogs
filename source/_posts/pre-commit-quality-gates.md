---
title: 把质量检查前移：pre-commit Hooks 的渐进落地清单
date: 2026-06-30 03:05:31
tags:
  - Git
  - 代码质量
  - 工程实践
  - 自动化
categories:
  - 工程实践
---

![pre-commit 质量闸口](/images/pre-commit-quality-gates/cover.jpeg)

代码质量问题越晚发现，修复成本越高。等 CI 跑红时，开发者已经切到下一个任务；等评审者指出格式、拼写、导入顺序或误提交密钥时，讨论也会被低价值噪声占满。`pre-commit` 的价值不是替代 CI，而是把确定、低成本、可自动修复的检查提前到 `git commit` 之前。

<!-- more -->

## 先定义边界

提交前检查适合处理三类事情：速度快、结果稳定、失败信息明确。格式化、lint、基础类型检查、YAML/JSON 语法、私钥和大文件扫描都很适合放进 hook。端到端测试、全量集成测试、长时间安全扫描则不适合，否则开发者迟早会绕过它。

| 检查项 | 适合放在 pre-commit 吗 | 原因 |
| --- | --- | --- |
| 代码格式化 | 适合 | 可自动修复，反馈快 |
| 密钥扫描 | 适合 | 越早拦截越安全 |
| 单文件 lint | 适合 | 只检查改动文件，成本可控 |
| 全量 E2E | 不适合 | 太慢，应交给 CI |
| 构建产物体积分析 | 谨慎 | 可放在 CI 阶段 |

一个实用原则是：hook 失败后，开发者应该能在一分钟内理解并修复。如果做不到，就先放进 CI，并在 PR 里给出报告。

## 一份最小配置

![提交前检查流程](/images/pre-commit-quality-gates/hook-flow.jpeg)

`pre-commit` 用仓库内的 `.pre-commit-config.yaml` 描述检查项。团队成员只需要安装一次 hook，之后每次提交都会对暂存区文件执行检查。

### 本地安装

```bash
pipx install pre-commit
pre-commit install
pre-commit run --all-files
```

### 配置文件

下面是一份偏通用的配置，覆盖文本格式、配置文件语法、私钥扫描和前端格式化：

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
      - id: check-json
      - id: detect-private-key
      - id: check-added-large-files

  - repo: local
    hooks:
      - id: prettier
        name: prettier
        entry: npx prettier --write
        language: system
        files: "\\.(js|ts|tsx|json|md|yml|yaml)$"
```

外部 hook 的版本号要固定在配置里，本地命令的版本则交给项目锁文件控制。升级工具应该像升级依赖一样进入评审，而不是让每个人机器上的环境自由漂移。

## 和 CI 共用同一把尺

pre-commit 只在本地跑是不够的。新同事可能忘记安装，紧急修复时也可能使用 `--no-verify`。更稳的做法是在 CI 里执行 `pre-commit run --all-files`，确保主干最终仍由自动化兜底。本地 hook 负责缩短反馈回路，CI 负责保证规则没有被绕过。两者共用同一份配置，团队就不用维护“两套差不多但总有差异”的质量标准。

## 渐进落地，别一次卡死

![渐进落地看板](/images/pre-commit-quality-gates/rollout-board.jpeg)

历史仓库接入 hook 时，最容易踩的坑是一次性打开所有检查，然后得到几百个失败。更好的节奏是先建立基线：第一阶段只启用格式和文件检查；第二阶段加入 lint，但只检查改动文件；第三阶段再把核心目录纳入更严格规则。

如果必须处理旧问题，可以单独开“清理 PR”，避免把业务改动和机械格式化混在一起。评审者能快速确认清理范围，也不容易在大 diff 里漏掉真实逻辑变化。

团队还应该约定例外规则：什么时候允许 `--no-verify`，跳过后要不要补跑 CI，密钥扫描误报如何登记。规则写清楚后，hook 才是协作工具。

## 日常维护清单

pre-commit 配好后也需要维护。每月或每个迭代升级一次 hook 版本，观察失败率和耗时；超过两三分钟的检查要么优化范围，要么挪到 CI；新增语言或目录时同步补规则；删除废弃工具时清理配置。最终目标不是“提交前跑得越多越好”，而是把重复、确定、低价值的问题自动拦住，把人的注意力留给设计、边界和业务语义。
