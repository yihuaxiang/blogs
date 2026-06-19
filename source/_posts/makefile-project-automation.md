---
title: 用 Makefile 统一项目任务入口：从本地脚本到 CI 复用
date: 2026-06-19 22:15:05
tags:
  - Makefile
  - 自动化
  - 工程实践
  - CI
categories:
  - 工程实践
---

![用 Makefile 统一项目任务入口](/images/makefile-project-automation/cover.jpeg)

项目越做越久，常见命令就越容易散落：README 里一段、`package.json` 里几段、CI 配置里再复制几段。新人要先问“怎么跑测试”，老成员也会在不同终端历史里找命令。Makefile 的价值不只是编译 C 项目，它还能给任何仓库提供一个稳定、可读、可组合的任务入口。

<!-- more -->

## 什么时候值得加 Makefile

如果项目只有一个 `npm test`，暂时不需要引入额外文件。但只要出现“安装、格式化、检查、测试、构建、清理、发布预检”这类成套动作，Makefile 就很合适。它把命令命名成目标，让人记 `make test`，而不是记一串带参数的脚本。

| 场景 | Makefile 带来的收益 |
| --- | --- |
| 多语言仓库 | 用同一入口封装 Node、Go、Python 等工具 |
| 新人启动项目 | `make setup` 比长文档更直接 |
| CI 与本地重复 | CI 调用同一个 `make ci`，减少配置漂移 |
| 命令带参数 | 用变量暴露少量可调项，避免复制粘贴 |

## 从可发现的目标开始

![项目任务入口](/images/makefile-project-automation/task-board.jpeg)

一个可维护的 Makefile，第一步不是写很多自动化，而是让目标自己可发现。常见做法是把 `help` 设为默认目标，并在每个公共目标后写一段说明：

```makefile
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

.PHONY: help setup lint test build ci clean

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "%-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install dependencies
	npm ci

lint: ## Run static checks
	npm run lint

test: ## Run test suite
	npm test

build: ## Build production assets
	npm run build

ci: setup lint test build ## Run the same checks as CI

clean: ## Remove generated files
	rm -rf dist coverage
```

这里有两个细节很重要。第一，Makefile 的命令行必须用 Tab 缩进，不是空格。第二，`.PHONY` 表示这些目标不是同名文件，避免仓库里出现 `test` 文件后 `make test` 被误判为已完成。

## 把依赖关系写出来

Makefile 比普通 shell 脚本强的一点，是目标之间可以声明依赖。上面的 `ci: setup lint test build` 表示执行 `make ci` 时，Make 会按顺序完成这些前置目标。对人来说，这是一份可读的流程图；对 CI 来说，这是一份可以直接复用的合约。

如果某些任务真的会生成文件，也可以让 Make 利用时间戳减少重复工作：

```makefile
dist/app.js: src/index.js package-lock.json
	npm run build

build: dist/app.js
```

这类写法适合“输入文件不变就不重建”的场景。但在前端、后端应用项目里，很多目标只是动作，不一定对应产物。不要为了显得高级而把所有命令都伪装成文件依赖，公共任务保持 `.PHONY` 往往更清楚。

## 用变量暴露少量开关

![依赖关系与构建缓存](/images/makefile-project-automation/dependency-graph.jpeg)

变量适合承载环境、路径和少量参数，例如：

```makefile
ENV ?= dev
PORT ?= 3000

serve: ## Start local server
	APP_ENV=$(ENV) PORT=$(PORT) npm run dev
```

这样默认执行 `make serve` 就能跑开发环境，需要临时改端口时写 `make serve PORT=4000`。变量不要滥用：如果一个目标需要十几个参数，说明它可能应该沉到专门的脚本、配置文件或程序入口里，Makefile 只负责调度。

## 让 CI 调用 Make，而不是复制命令

CI 配置最怕和本地命令分叉。更稳的方式是把业务动作留在 Makefile，CI 只负责选择运行环境：

```yaml
steps:
  - run: npm ci
  - run: make ci
```

如果 `setup` 已经包含安装依赖，也可以只保留 `make ci`。关键是避免在 CI 里重新写一套 `npm run lint && npm test && npm run build`。当检查流程改变时，只改 Makefile，本地和 CI 同时生效。

## 几条容易踩坑的边界

Makefile 不是万能任务系统。跨平台项目要注意 Windows 环境是否有 `make` 和 Bash；涉及密钥的命令不要把 token 写进文件，应从环境变量读取；高风险目标最好命名得明确，例如 `deploy-prod`，并在命令里加确认或依赖外部审批。还要控制目标数量，常用入口放在顶层，冷门维护脚本可以放到 `scripts/` 目录，再由 Makefile 包一层短命令。

## 一份实践清单

给现有项目补 Makefile，可以按这个顺序推进：

1. 先收集 README、CI、团队文档里的常用命令。
2. 只抽取最常用的 6 到 10 个目标，先覆盖 `setup`、`lint`、`test`、`build`、`ci`。
3. 给每个公共目标写 `##` 注释，并让 `make` 默认显示帮助。
4. 让 CI 调用同一组目标，删除重复命令。
5. 把复杂逻辑留给脚本或语言工具，Makefile 只做入口和编排。

好的 Makefile 不追求炫技，而是让项目每天都会用到的动作变短、变稳、变统一。当所有人都能从 `make help` 开始，命令就不再藏在个人经验里，而成为仓库的一部分。
