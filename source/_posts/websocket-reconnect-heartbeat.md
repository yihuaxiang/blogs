---
title: WebSocket 长连接别只会重连：心跳、退避与消息幂等
date: 2026-11-13 03:02:10
tags:
  - WebSocket
  - 网络编程
  - 可靠性
categories:
  - 工程实践
---

![WebSocket 可靠连接示意图](/images/websocket-reconnect-heartbeat/cover.jpeg)

WebSocket 建立连接容易，难的是切网、休眠、代理超时和服务重启后仍能恢复。只监听 `close` 并立即重连，会制造连接风暴，也可能重复提交消息。下面用三个机制把长连接做稳。

<!-- more -->

## 先定义连接状态

不要让业务代码直接操作裸 socket。把连接封装成 `idle`、`connecting`、`open`、`backoff`、`closed` 状态。每次连接分配递增的 `generation`，旧连接晚到的事件不能修改新连接。

```js
let generation = 0;
let socket;

function connect() {
  const current = ++generation;
  socket = new WebSocket(url);
  socket.onopen = () => current === generation && onOpen();
  socket.onmessage = (event) => {
    if (current === generation) onMessage(event.data);
  };
  socket.onclose = () => {
    if (current === generation) scheduleReconnect();
  };
}
```

页面关闭时置为 `closed` 并清理定时器，避免后台继续创建连接。

## 心跳要验证“可用”，不只是“没断”

TCP 连接存在，不代表应用仍可收发数据。切网、NAT 过期或代理空闲超时会造成半开状态。客户端定时发送 ping，服务端返回 pong 并记录时间；超过阈值无响应就主动关闭，交给重连流程。

![心跳检测示意图](/images/websocket-reconnect-heartbeat/heartbeat.jpeg)

间隔应小于最短空闲超时，并加入随机抖动，避免大量连接同时发送。浏览器回到前台时应立即探测。

## 重连使用指数退避和上限

断开后立即重试会放大故障。第 `n` 次重试延迟可以写成：

```js
const base = 500;
const cap = 30_000;
const delay = Math.min(cap, base * 2 ** attempt);
const jitter = delay * (0.5 + Math.random());
setTimeout(connect, jitter);
```

成功后把 `attempt` 清零；鉴权失败时停止无限重连，转为需要用户操作，并提供“立即重试”。

## 让消息具备幂等性

重连只能恢复通道，不能保证只处理一次。每条命令带唯一 `messageId`，服务端在事务中记录已处理 ID，重复请求返回同一结果。需要顺序时增加序号或游标，恢复后从最后确认处补发。

![消息幂等处理示意图](/images/websocket-reconnect-heartbeat/idempotency.jpeg)

| 场景 | 客户端动作 | 服务端约束 |
| --- | --- | --- |
| 连接建立 | 发送身份和最后确认游标 | 校验会话，返回可补发范围 |
| 普通命令 | 携带 `messageId` | 去重并持久化处理结果 |
| 连接异常 | 暂停发送，进入退避 | 保留未确认命令的可重试语义 |

## 上线前检查清单

记录连接代次、断开原因、重连次数、心跳延迟和待确认数，才能区分服务端与网络问题。测试覆盖切网、休眠恢复、代理超时、滚动重启和重复 `messageId`。四层边界清晰后，WebSocket 才适合实时协作与通知。
