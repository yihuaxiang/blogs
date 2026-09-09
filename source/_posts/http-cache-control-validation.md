---
title: HTTP 缓存别靠猜：Cache-Control、Vary 与条件请求实战
date: 2026-09-10 03:01:36
tags:
  - HTTP
  - 缓存
  - Web 性能
  - CDN
categories:
  - 工程实践
---

![浏览器、CDN 与源站组成的 HTTP 缓存链路](/images/http-cache-control-validation/cover.jpeg)

页面明明更新了，用户却仍看到旧内容；接口接入 CDN 后，不同用户的数据竟然互相串了；静态资源每次访问都重新下载。此类问题往往不是“缓存失效了”，而是响应没有说清楚谁能缓存、能用多久，以及哪些请求对应同一份表示。HTTP 已提供完整的表达方式，关键是把策略写成可验证的响应契约。

<!-- more -->

## 先区分新鲜度与可存储性

浏览器是私有缓存，CDN 和反向代理通常是共享缓存。`Cache-Control` 同时约束两类缓存，其中最容易混淆的是 `no-cache` 与 `no-store`：前者允许保存，但再次使用前必须向源站验证；后者要求不要存储响应。

| 指令 | 含义 | 常见场景 |
| --- | --- | --- |
| `max-age=300` | 响应在 300 秒内新鲜 | 可短暂复用的公开内容 |
| `s-maxage=60` | 只覆盖共享缓存的新鲜时间 | CDN 缓存时间短于浏览器 |
| `private` | 只允许私有缓存保存 | 登录后的个性化页面 |
| `no-cache` | 可保存，每次复用前需验证 | 希望节省响应体传输的 HTML |
| `no-store` | 不应保存响应 | 高敏感或一次性数据 |

例如带内容哈希的资源名不会原地改变，可以放心长期缓存：

```http
Cache-Control: public, max-age=31536000, immutable
```

入口 HTML 则应快速感知发布，可采用：

```http
Cache-Control: no-cache
ETag: "home-a81f"
```

不要给所有响应统一追加长 `max-age`。URL 不变但内容会变的文件一旦进入多层缓存，仅修改源站文件并不能保证立刻生效。

## 用条件请求低成本确认变化

![条件请求命中与未命中的响应分支](/images/http-cache-control-validation/revalidation-flow.jpeg)

响应过期不等于必须重新下载。缓存保存 `ETag` 后，可在后续请求中携带 `If-None-Match`：

```http
GET /app-config.json HTTP/1.1
If-None-Match: "cfg-42"
```

表示未改变时，源站返回不含响应体的 `304 Not Modified`；已改变则返回新的 `200`、响应体和校验器。`Last-Modified` 与 `If-Modified-Since` 也能完成验证，但修改时间可能精度不足，且不一定能代表内容身份。两组条件同时出现时，应让 `If-None-Match` 的判断优先。

ETag 必须跟“实际返回的表示”绑定。若压缩层会为 gzip 和 br 生成不同字节，校验器与缓存键都要能区分它们，不能让不同表示错误共享同一个强 ETag。

## Vary 决定缓存键的维度

![由请求头维度拆分出的缓存键](/images/http-cache-control-validation/cache-key.jpeg)

同一 URL 可能根据请求头返回不同内容。`Vary` 告诉缓存：除 URL 外，还要把指定请求头纳入匹配条件。例如服务端按压缩能力选择响应时，应返回：

```http
Vary: Accept-Encoding
```

跨域响应若随请求的 `Origin` 改变，也应正确设置 `Vary: Origin`。但 `Vary` 不是越多越安全：把 `User-Agent`、完整 Cookie 或高离散度头字段放入其中，会制造大量几乎无法复用的缓存变体。

对用户专属 API，优先明确禁止共享缓存，而不是试图把用户身份塞进 `Vary`：

```http
Cache-Control: private, no-cache
Vary: Accept-Encoding
```

涉及令牌、支付结果或一次性下载地址且不应落盘时，使用 `no-store`。公开 API 才适合 `public` 与共享缓存；其缓存键还应覆盖确实影响结果的查询参数、语言和内容协商维度。

## 把发布与失效设计进 URL

长期缓存最可靠的失效手段不是批量清理 CDN，而是内容寻址。构建产物使用 `app.7d31c.js` 这类带哈希文件名，内容变化就产生新 URL；HTML 保持短缓存或强制验证，并引用新的资源地址。这样旧页面仍能加载旧资源，新页面也不会撞上旧缓存。

动态接口无法改 URL 时，才结合短 TTL、条件请求和 CDN purge。可用 `stale-while-revalidate` 在后台验证期间暂时返回旧响应，降低回源延迟，但业务必须先定义“旧多久仍可接受”，不能把它用于余额、库存扣减等实时决策。

## 用命令验证真实链路

不要只看框架配置，要检查经过网关和 CDN 后的最终响应：

```bash
curl -sS -D headers.txt -o body.json https://example.com/app-config.json
etag=$(sed -n 's/^etag: //Ip' headers.txt | tr -d '\r')
curl -sS -D - -o /dev/null -H "If-None-Match: $etag" \
  https://example.com/app-config.json
```

检查 `Cache-Control`、`ETag`、`Vary`、`Age` 和状态码，并分别用匿名、登录、不同压缩请求测试。自动化测试至少覆盖三件事：私有响应不会被共享，变体不会串内容，资源改变后旧校验器不会得到错误的 `304`。

一套可靠的 HTTP 缓存策略可以归结为四个问题：响应能否保存、谁能保存、新鲜多久、如何确认变化。先按资源类型回答它们，再配置浏览器、CDN 与源站，缓存才能成为可推理的性能组件，而不是靠刷新页面碰运气的黑盒。
