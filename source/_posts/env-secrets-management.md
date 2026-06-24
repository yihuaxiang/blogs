---
title: 把密钥管住：从 .env 到 CI Secrets 的配置安全清单
date: 2026-06-25 03:06:46
tags:
  - 配置管理
  - 安全
  - CI
  - DevOps
categories:
  - 工程实践
---

![把密钥管住](/images/env-secrets-management/cover.jpeg)

很多项目的第一份配置都很简单：复制一份 `.env`，填上数据库地址、API Key 和调试开关，应用就能跑起来。但项目一旦进入多人协作、CI 构建和线上发布，配置就不再只是“读几个环境变量”。真正要管住的是边界：哪些值可以进仓库，哪些只能留在本机，哪些必须由 CI 或运行时注入，以及密钥泄露后如何快速轮换。

<!-- more -->

## 先把配置分层

配置管理最常见的坑，是把“可公开配置”和“敏感密钥”混在一个文件里。更稳的做法是按用途分层，每一层只放它应该拥有的信息。

| 类型 | 例子 | 能否提交仓库 | 推荐位置 |
| --- | --- | --- | --- |
| 默认配置 | 端口、日志级别、分页大小 | 可以 | 代码或配置模板 |
| 配置样例 | 必填变量名、示例格式 | 可以 | `.env.example` |
| 本地密钥 | 开发数据库密码、测试 API Key | 不可以 | 本机 `.env` |
| CI 密钥 | 发布 Token、镜像仓库密码 | 不可以 | CI Secrets |
| 运行时密钥 | 生产数据库密码、第三方凭证 | 不可以 | Secret Manager 或平台变量 |

这个表的核心不是工具选择，而是职责边界。仓库负责告诉开发者“需要哪些配置”，本机负责开发便利性，CI 负责构建和发布，运行时负责生产访问。不要让一个 `.env` 文件承担所有环境的职责。

## `.env` 只做本地入口

![配置分层流转](/images/env-secrets-management/config-layers.jpeg)

`.env` 适合本地开发，因为它简单、可读、容易复制。但它不应该出现在 Git 历史、Docker 镜像、构建产物或日志里。仓库里应提交的是 `.env.example`，它只描述变量名和格式，不包含真实值。

```gitignore
.env
.env.*
!.env.example
```

```dotenv
# .env.example
APP_ENV=development
PORT=3000
DATABASE_URL=postgres://user:password@localhost:5432/app
PAYMENT_API_KEY=replace-me
```

样例文件里的值要明显是占位符，避免新人误以为它们能直接用于测试或生产。更进一步，可以在 README 或启动脚本里说明如何复制：

```bash
cp .env.example .env
```

这里不要把 `.env.production` 当作“生产配置模板”提交上来。生产环境的变量应该由部署平台、容器编排系统或密钥服务注入，而不是随代码分发。

## 启动时校验，而不是运行到一半再炸

配置错误最怕在用户请求进来后才暴露。例如数据库地址为空、布尔值写成 `yes`、超时时间被当成字符串拼接。应用启动时应该集中读取、校验和转换配置，业务代码只使用已经整理好的对象。

```js
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing environment variable: ${name}`);
  return value;
}

function numberFromEnv(name, fallback) {
  const raw = process.env[name] ?? String(fallback);
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    throw new Error(`Invalid number environment variable: ${name}`);
  }
  return value;
}

export const config = {
  appEnv: process.env.APP_ENV ?? "development",
  port: numberFromEnv("PORT", 3000),
  databaseUrl: required("DATABASE_URL"),
  paymentApiKey: required("PAYMENT_API_KEY")
};
```

这样做有两个好处：第一，缺少关键变量时应用直接启动失败，问题会在部署阶段暴露；第二，业务模块不会到处散落 `process.env`，后续改变量名、加默认值或做脱敏日志都更容易。

## CI Secrets 和运行时密钥分开

CI Secrets 适合保存“构建和发布需要的凭证”，例如推送镜像、上传静态资源、调用部署 API。它不应该默认拥有生产数据库密码。否则只要构建脚本、第三方 Action 或日志输出出问题，生产访问面就被放大了。

一个简单原则是：CI 能发布应用，但不直接读写生产数据；运行时能访问业务依赖，但不拥有发布权限。两类权限分开后，即使某一边出事故，影响范围也更可控。

| 场景 | 应该使用的密钥 | 不该顺手复用的密钥 |
| --- | --- | --- |
| 单元测试 | 测试服务凭证 | 生产数据库密码 |
| 构建镜像 | 镜像仓库 Token | 应用第三方支付 Key |
| 部署应用 | 部署平台 Token | 云账号全局 Root Key |
| 线上运行 | 生产依赖凭证 | CI 发布 Token |

在 CI 脚本里还要避免 `set -x`、完整打印环境变量、把配置写入构建产物。需要排障时，只输出变量是否存在、长度是否合理、当前使用的是哪个环境，不输出真实内容。

## 泄露后的轮换流程要提前写好

![密钥轮换流程](/images/env-secrets-management/rotation-flow.jpeg)

密钥管理不是把值藏起来就结束了。真正考验系统的是泄露之后能不能快速止血。建议为每类关键密钥准备一份轮换流程，至少包含四步：

1. 确认泄露范围：是哪一个变量、哪个环境、哪段时间暴露。
2. 立即吊销旧值：先阻断继续使用，再分析根因。
3. 发布新值：在 CI Secrets 或运行时平台更新变量，并触发部署。
4. 验证和清理：确认服务正常，删除日志、工单或文档里的残留密钥。

如果第三方平台支持多 Key 并存，可以先创建新 Key、切流验证，再删除旧 Key；如果只支持单 Key，就要准备短暂停机或灰度窗口。无论哪种方式，轮换动作都应该能在几分钟内被执行，而不是事故发生后才去找入口。

## 最后留一张检查清单

每个项目都可以用下面这张表做自检：

| 检查项 | 合格标准 |
| --- | --- |
| 仓库 | 只有 `.env.example`，没有真实 `.env` |
| 启动 | 缺少必填变量时立即失败 |
| 日志 | 不打印 Token、密码、连接串完整内容 |
| CI | 发布凭证和运行时凭证分离 |
| 权限 | 密钥按环境、按用途最小化授权 |
| 轮换 | 能快速吊销、替换、验证和清理 |

配置安全不是一次性整理，而是一套默认习惯。把变量分层、把样例留在仓库、把真实密钥交给对应环境、把校验放到启动阶段，再补上可执行的轮换流程，项目进入多人协作和持续发布时就不会被一份随手复制的 `.env` 拖住。
