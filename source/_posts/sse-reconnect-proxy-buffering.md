---
title: 把 SSE 接稳：断线续传、心跳与代理缓冲
date: 2026-08-12 03:02:54
tags:
  - SSE
  - HTTP
  - 后端
  - 实时通信
categories:
  - 工程实践
---

![稳定的 SSE 事件流](/images/sse-reconnect-proxy-buffering/cover.jpeg)

订单状态、构建日志和后台任务进度这类信息，往往只需要服务端持续推给页面。Server-Sent Events（SSE）比双向协议更轻：一个普通 HTTP 响应保持打开，浏览器的 `EventSource` 会在连接中断后尝试重连。但“能收到消息”不等于“稳定送达”。代理缓冲、空闲超时和重连窗口里的事件缺口，都会让页面看起来偶发失效。

<!-- more -->

## 先把交付语义说清楚

SSE 是服务端到客户端的单向事件流。每条事件以空行结束；`id` 是可续传的游标，`event` 用来区分类型，`data` 承载数据。服务端应明确返回流式响应，而不是把事件攒成一个 JSON 数组再一次性输出。

```text
id: 8421
event: build.updated
data: {"buildId":"b-17","status":"passed"}

```

浏览器收到带 `id` 的事件后，会在重连请求中携带 `Last-Event-ID`。这提供的是“从某个位置继续读”的机会，不是恰好一次送达保证：连接在响应已写出、浏览器尚未处理时断开，重放就可能造成重复；服务端只保留有限历史时，也可能无法补齐过旧游标。因此，页面应使用事件 ID 去重；触发扣款、提交等副作用时，仍须调用具备幂等约束的普通 API。

| 目标 | 需要的机制 | 不该依赖的假设 |
| --- | --- | --- |
| 断线后尽量补消息 | 单调递增 ID 与短期事件保留 | 单条 TCP 连接永远不断 |
| 避免页面重复渲染 | 按事件 ID 去重或按版本覆盖 | 重连后一定不会重复 |
| 处理历史过期 | 返回快照或要求页面全量刷新 | 任意旧游标都能重放 |

## 用游标和保留窗口补齐缺口

![事件日志与重连后的续传](/images/sse-reconnect-proxy-buffering/event-replay.jpeg)

事件 ID 必须来自可排序、可查询的来源，例如数据库递增序列或按分区定义的游标；不要用本机时间戳冒充全局顺序。连接建立时读取 `Last-Event-ID`，并由事件库原子地确定“历史回放到哪个尾部游标、从哪个游标开始订阅”。补发范围与开始订阅的边界要在同一套游标规则下确定，避免“先查完历史、再订阅”之间漏掉新消息。

下面的写入函数只负责生成正确的帧。生产代码还要限制单条消息大小，并在客户端断开后取消订阅和计时器。

```js
function writeEvent(res, message) {
  res.write(`id: ${message.id}\n`);
  res.write(`event: ${message.type}\n`);
  res.write(`data: ${JSON.stringify(message.payload)}\n\n`);
}

app.get("/events", async (req, res) => {
  res.set({
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no"
  });
  const after = req.get("Last-Event-ID") || "0";
  // 原子地回放 (after, tail]，再从 tail 继续订阅。
  const cursor = eventStore.openStreamAfter(after);
  req.on("close", () => cursor.close());
  for await (const message of cursor) writeEvent(res, message);
});
```

若 `after` 已早于保留窗口，最好用一个明确的事件或 HTTP 状态告诉客户端“历史不可用”，随后拉取当前快照。静默从最新事件继续，页面最容易悄悄缺数据。

## 让代理真的把事件立即转发

![心跳穿过反向代理的长连接](/images/sse-reconnect-proxy-buffering/proxy-heartbeat.jpeg)

很多“服务端明明写了，页面却不更新”的根因在反向代理：它为了吞吐而缓存上游响应，直到积累到一定大小才发给浏览器。应用响应带上 `X-Accel-Buffering: no`，并在 Nginx 对 SSE 路径显式关闭缓冲；其他网关也要检查等价设置。不要为流式响应设置 `Content-Length`。

```nginx
location /events {
  proxy_pass http://app;
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_buffering off;
  proxy_read_timeout 1h;
}
```

还需要根据网关、负载均衡和 CDN 中最短的空闲超时设置心跳。服务端可定期写入 SSE 注释帧 `: ping\n\n`：浏览器会忽略它，但链路上的设备会看到流量。心跳间隔应小于最短空闲超时，并在连接关闭时停止；把它写得过于频繁只会放大连接数带来的额外流量。

## 客户端保持简单，排障覆盖断线

原生 `EventSource` 会自动重连，不必在每次 `error` 时立刻新建多个连接。页面销毁时调用 `close()`；接收事件时使用 `lastEventId` 或业务版本防重。对状态类信息，按版本覆盖通常比累加更安全。

```js
const stream = new EventSource("/events");
stream.addEventListener("build.updated", (event) => {
  const update = JSON.parse(event.data);
  if (isNewer(event.lastEventId)) renderBuild(update);
});

window.addEventListener("pagehide", () => stream.close());
```

| 现象 | 优先检查 |
| --- | --- |
| 事件隔很久才成批出现 | 用 `curl -N` 直连和经代理各测一次，检查缓冲 |
| 每隔固定时间反复重连 | 对照网关空闲超时和心跳间隔 |
| 页面状态跳回旧值 | 检查重放去重与版本比较 |
| 发布后部分用户缺事件 | 检查保留窗口、历史过期分支和快照接口 |

上线前至少演练四件事：浏览器断网再恢复、代理路径下的即时刷新、服务实例替换时的重连，以及游标早于保留窗口。SSE 的价值不在于保持一条“永不掉线”的连接，而在于掉线后仍能以可解释、可恢复的方式回到正确状态。
