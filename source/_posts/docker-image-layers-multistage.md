---
title: Docker 镜像分层与多阶段构建：体积、缓存与供应链
date: 2026-11-09 03:02:43
tags:
  - Docker
  - 容器
  - 构建优化
categories:
  - 工程实践
---

![Docker 镜像分层与多阶段构建](/images/docker-image-layers-multistage/cover.jpeg)

镜像能否快速构建、稳定发布，取决于 Dockerfile 如何组织层、缓存和运行时文件。

<!-- more -->

## 1. 镜像不是一个压缩包

Docker 镜像由多个只读层叠加而成，每条大多数 Dockerfile 指令都会产生新层。容器启动时再叠加一个可写层。层具有内容摘要，同样的输入可以复用缓存；某一层失效后，后续层通常也要重新执行。

![镜像层与缓存](/images/docker-image-layers-multistage/layers.jpeg)

因此，下面两种写法的结果不同：

```dockerfile
# 先复制依赖清单，依赖不变时可复用安装层
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY . .

# 不要先复制整个仓库再安装依赖
```

源码放在后面可减少重复下载；`.dockerignore` 应排除 `.git`、构建产物、日志和本地依赖目录。

## 2. 多阶段构建隔离工具链

编译器、测试框架和源码只在构建阶段需要，运行时通常只需要一个二进制文件或打包后的静态资源。多阶段构建用多个 `FROM` 描述阶段，再从前一阶段复制产物：

```dockerfile
FROM golang:1.23 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /out/server ./cmd/server

FROM gcr.io/distroless/static-debian12
COPY --from=builder /out/server /server
USER 65532:65532
ENTRYPOINT ["/server"]
```

第二阶段只复制明确产物，减少体积与攻击面。

![多阶段构建](/images/docker-image-layers-multistage/multistage.jpeg)

## 3. 缓存设计要有边界

下载依赖时应提交锁文件，并让构建命令使用锁文件。BuildKit 可用缓存挂载减少重复下载：

```dockerfile
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    go build -o /out/server ./cmd/server
```

## 4. 用可观测数据验收镜像

用以下指标检查优化结果：

| 检查项 | 关注点 |
| --- | --- |
| `docker history` | 层大小与意外文件 |
| 拉取大小 | 发布成本 |
| SBOM/扫描 | 系统包与漏洞 |

## 5. 上线前检查

确认 `.dockerignore` 已排除元数据和依赖目录；依赖使用锁文件；运行阶段使用非 root 用户；基础镜像来源和版本策略可追溯；CI 同时验证构建、启动与扫描结果。


镜像优化的核心是让输入、缓存和运行时边界清楚，并用构建与启动结果验证每次改动。
