---
title: 用 Docker Compose 打造可维护的本地开发环境
date: 2026-06-22 03:03:03
tags:
  - Docker
  - Docker Compose
  - 本地开发
categories:
  - 工程实践
---

![Docker Compose 本地开发环境](/images/docker-compose-local-dev-env/cover.jpeg)

本地环境最怕两件事：新同事跑不起来，老项目没人敢动。Docker Compose 的价值不只是“一条命令启动数据库”，而是把应用、依赖、端口、数据卷和调试入口写成可以审查的工程契约。下面这份清单适合中小型 Web 项目，也适合把散落的 README 步骤收敛成稳定入口。

<!-- more -->

## 先定边界：哪些东西应该进 Compose

Compose 文件不是生产编排平台的替代品，也不应该把所有运维细节塞进去。它最适合描述“开发时必须一起存在”的服务：应用进程、数据库、缓存、消息队列、对象存储模拟器，以及少量调试工具。

| 内容 | 建议 | 原因 |
| --- | --- | --- |
| 应用和强依赖 | 放进 Compose | 降低启动成本，便于统一网络 |
| 本地编辑器、IDE | 不放 | 每个人偏好不同 |
| 生产证书和真实密钥 | 不放 | 防止泄漏和误连 |
| 临时排障工具 | 用 profile 控制 | 避免默认启动过重 |

一个好用的判断标准是：如果新人需要照着 README 手动安装，它很可能应该被容器化；如果它只和个人工作流有关，就不必写进共享配置。

## 一个可维护的 compose.yaml

下面的示例用 `app`、`db`、`cache` 三个服务展示基本结构。重点不是镜像名字，而是几个约定：服务名就是内网主机名，数据库用健康检查声明“真的可用”，代码目录用 bind mount 保持热更新，依赖目录用命名卷避免被宿主机污染。

```yaml
services:
  app:
    build: .
    command: npm run dev
    working_dir: /workspace
    ports:
      - "3000:3000"
    environment:
      DATABASE_URL: postgres://app:app@db:5432/app
      REDIS_URL: redis://cache:6379
    volumes:
      - .:/workspace
      - node_modules:/workspace/node_modules
    depends_on:
      db:
        condition: service_healthy
      cache:
        condition: service_started

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 3s
      retries: 20

  cache:
    image: redis:7-alpine

volumes:
  pgdata:
  node_modules:
```

这里有三个细节值得保留。第一，应用连接数据库时使用 `db:5432`，不要写 `localhost`，因为容器之间走的是 Compose 创建的内部网络。第二，`depends_on` 只能表达启动顺序，配合 `service_healthy` 才能减少“数据库进程启动了但还不能接请求”的竞态。第三，数据卷要有名字，删除容器不会误删开发数据，真正需要重置时再显式执行清理命令。

![服务依赖与数据卷示意](/images/docker-compose-local-dev-env/service-map.jpeg)

## 配置要分层，不要把秘密写死

开发配置建议分成三层：提交到仓库的默认值、每个人本地的覆盖值、运行时注入的敏感值。可以提交 `.env.example`，但不要提交真实 `.env`。

```bash
# .env.example
APP_PORT=3000
POSTGRES_USER=app
POSTGRES_PASSWORD=app
POSTGRES_DB=app
```

在 Compose 中引用变量时，给非敏感项提供默认值，给敏感项保留显式失败空间：

```yaml
services:
  app:
    ports:
      - "${APP_PORT:-3000}:3000"
    environment:
      DATABASE_URL: postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
```

这样做的好处是，本地端口冲突可以快速改 `.env`，而真实密钥不会因为“图省事”进入 Git 历史。团队还可以把 `.env.example` 当作配置文档，每新增一个变量就同步说明默认值和用途。

## 把日常命令收敛成固定入口

Compose 的命令也要少而稳定。团队可以把常用动作写进 `Makefile` 或 README，但底层命令保持一致：

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f app
docker compose exec app npm test
docker compose down
docker compose down -v
```

其中 `down` 和 `down -v` 要区分清楚：前者停止并删除容器、网络，通常不会清掉命名卷；后者会连开发数据一起删除，适合重建数据库结构或清理脏数据。这个差异最好写进文档，否则排障时很容易把“重启环境”变成“清空环境”。

## 用 profile 管理可选工具

不是每个服务都应该默认启动。例如数据库管理界面、邮件捕获器、性能分析工具，只有少数场景需要。可以用 `profiles` 做成按需启动：

```yaml
services:
  adminer:
    image: adminer
    ports:
      - "8080:8080"
    profiles:
      - tools
```

日常只跑核心依赖：

```bash
docker compose up -d
```

需要工具时再打开：

```bash
docker compose --profile tools up -d
```

这样既保留了排障能力，又不会让默认环境变慢、变吵。

## 自检清单

写完 Compose 文件后，可以按下面顺序检查：服务名是否稳定，端口是否只暴露必要入口，数据库是否有健康检查，数据卷是否命名，`.env.example` 是否覆盖所有变量，危险命令是否有说明。最后让一个干净目录或新同事账号从零执行一次启动流程，比作者自己反复试更能暴露隐性依赖。

可维护的本地环境不追求复杂，而追求可复制、可解释、可重置。把这些约定沉淀到 `compose.yaml` 后，README 会变短，排障会更直接，团队也能把注意力放回业务代码。
