---
title: HTTP 103 Early Hints：让浏览器更早开始加载关键资源
date: 2026-10-31 03:02:40
tags:
  - HTTP
  - Web 性能
  - 浏览器
categories:
  - 工程实践
---

![服务器向浏览器提前发送资源提示](/images/http-early-hints-performance/cover.jpeg)

一个页面的 HTML 可能要等数据库查询、模板渲染和权限判断完成后才能返回，但浏览器真正需要的 CSS、字体和脚本往往早已确定。HTTP 103 Early Hints 允许服务器在最终响应之前，先把这些资源提示给浏览器，让网络传输和服务端计算并行发生。

<!-- more -->

## 103 到底提前了什么

一次普通请求通常只有一个最终响应：服务器准备好 HTML 后返回 `200 OK`，浏览器解析到 `<link rel="stylesheet">` 才发起 CSS 请求。若后端耗时 600 毫秒，关键资源就被迫晚 600 毫秒出发。

Early Hints 是一个临时响应。服务器先发送 `103`，在响应头中放置 `Link`，随后仍然发送正常的 `200`、`304` 或错误响应：

```http
HTTP/1.1 103 Early Hints
Link: </assets/app.css>; rel=preload; as=style
Link: </assets/app.js>; rel=preload; as=script

HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
```

浏览器收到 103 后可以提前建立连接、下载资源；最终响应才决定页面是否成功以及正文是什么。103 不会替代最终响应，也不应该承载业务数据。

![Early Hints 将等待时间变成并行下载](/images/http-early-hints-performance/timeline.jpeg)

## 先找出值得提示的资源

提示过多会抢占 HTML 本身的带宽，甚至让首屏变慢。建议只选择同时满足以下条件的资源：

| 条件 | 判断方法 |
| --- | --- |
| 每个页面都需要 | 不能只在少数用户分支出现 |
| 体积适中且优先级高 | 首屏 CSS、关键字体通常比图片更合适 |
| URL 稳定 | 带内容哈希的静态文件更容易长期缓存 |
| 不依赖用户秘密 | 不要把订单、私有头像或个性化接口放进提示 |

`preload` 必须写正确的 `as`，否则浏览器可能重复下载或采用错误的缓存分区。跨域字体还要保持 `crossorigin` 与实际请求一致：

```http
Link: </fonts/inter.woff2>; rel=preload; as=font; crossorigin
```

如果资源只是“可能用到”，使用 `preconnect` 或普通的文档声明通常更稳。Early Hints 的价值在于缩短确定资源的等待时间，而不是替猜测做全量预取。

## 在应用层构造 103

不同服务器和代理对临时响应的 API 不同，应用层应把提示列表作为页面元数据生成，并交给最外层 HTTP 服务发送。以支持临时响应的 Node.js 风格接口为例：

```js
const hints = [
  '</assets/app.css>; rel=preload; as=style',
  '</assets/app.js>; rel=preload; as=script'
];

res.writeEarlyHints({ link: hints });
const html = await renderPage(request);
res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
res.end(html);
```

生产环境要确认 CDN、反向代理和 HTTP/2/HTTP/3 链路不会删除 103。可以用抓包或 `curl -I` 检查响应链，但单次命令未必能展示所有临时响应，最好在浏览器网络面板和边缘节点日志中同时观察。

## 缓存、回退与观测

客户端不支持 103 时，应当像没有 Early Hints 一样正常工作，所以不能把关键逻辑放在 103 中。提示头也要服从资源的缓存策略；部署新版本时，优先使用带哈希的文件名，避免旧缓存与新 HTML 组合。

![缓存命中与源站回退的选择](/images/http-early-hints-performance/cache-decision.jpeg)

上线前可以建立三组指标：首个字节时间、关键 CSS 请求开始时间、首屏可交互时间。对比开启前后的中位数和高分位数，并按缓存命中、设备网络和页面模板分组。如果 CSS 开始得更早却没有带来首屏收益，常见原因是提示了非关键资源、带宽被提前请求占满，或代理根本没有转发 103。

Early Hints 适合“服务端准备慢、关键资源确定早”的页面。先用少量静态资源做 A/B 验证，再逐步扩展提示清单，才能把协议能力变成可测量的性能收益。
