---
title: 把日志写到能排障：结构化日志、Trace ID 与脱敏清单
date: 2026-06-21 03:03:55
tags:
  - 日志
  - 可观测性
  - 后端
  - 工程实践
categories:
  - 工程实践
---

![把日志写到能排障](/images/structured-logging-trace-id/cover.jpeg)

很多系统不是没有日志，而是日志只能证明“代码曾经跑过”。真正能排障的日志，应该在事故发生时回答三个问题：这是谁的请求、走到了哪一步、为什么变慢或失败。把日志从散乱句子升级为结构化事件，再配合 Trace ID、字段规范和脱敏策略，排查效率会比临时翻字符串高很多。

<!-- more -->

## 先定义一条好日志的标准

一条好日志不是越长越好，而是字段稳定、语义清楚、能被机器查询。人读的是 `message`，检索、聚合和告警依赖的是字段。

| 字段 | 作用 | 示例 |
| --- | --- | --- |
| `timestamp` | 还原时间线 | `2026-06-21T03:03:55Z` |
| `level` | 区分严重程度 | `info` / `warn` / `error` |
| `event` | 标识业务事件 | `order.created` |
| `trace_id` | 串起一次请求 | `7b4f...` |
| `duration_ms` | 定位慢步骤 | `128` |
| `error_code` | 聚合同类失败 | `PAYMENT_TIMEOUT` |

关键点是“字段名要长期稳定”。如果今天叫 `requestId`，明天叫 `trace_id`，后天塞进一句中文描述里，日志平台就很难建立可靠查询。

## 不把句子当协议

![结构化日志管道](/images/structured-logging-trace-id/log-pipeline.jpeg)

很多项目一开始会写这样的日志：

```js
logger.info(`user ${user.id} created order ${order.id}`);
```

这行日志看起来可读，但当你想统计某个用户的下单失败率、按订单号过滤、或按耗时排序时，就只能依赖字符串匹配。更稳的写法是把事件和字段分开：

```js
logger.info({
  event: "order.created",
  trace_id: ctx.traceId,
  user_id: user.id,
  order_id: order.id,
  amount_cents: order.amountCents,
  duration_ms: Date.now() - startedAt
}, "order created");
```

`message` 负责让人快速扫一眼，结构化字段负责查询。字段值也要尽量选择离散、可聚合的内容，例如 `event`、`error_code`、`payment_channel`，不要把完整异常堆栈、HTTP body 或 SQL 全部塞进每条业务日志。

## Trace ID 要贯穿完整链路

![Trace ID 排障路径](/images/structured-logging-trace-id/debug-flow.jpeg)

Trace ID 的价值在于把入口请求、服务调用、数据库操作、队列任务和异步回调串成一条线。入口层如果没有收到调用方传来的请求标识，就生成一个；后续调用下游、投递消息、写入任务表时都要继续传递。

```js
import { randomUUID } from "node:crypto";

function createContext(req) {
  const traceId = req.headers["x-request-id"] || randomUUID();
  return { traceId };
}

async function publishJob(ctx, order) {
  await queue.send({
    type: "sync_order",
    trace_id: ctx.traceId,
    order_id: order.id
  });
}
```

异步任务最容易断链。很多事故排查到队列就失去上下文，只能靠时间猜测。把 `trace_id` 放进消息体或任务元数据里，消费者处理时继续写入日志，才能从一次用户操作追到后台补偿任务。

## 脱敏要在写日志前完成

日志经常被更多人、更长时间地访问，所以不能把它当成内部安全区。密码、验证码、访问令牌、Cookie、完整身份证号、银行卡号都不应该进入日志。邮箱、手机号、姓名这类个人信息也要按业务需要做掩码、哈希或只记录内部 ID。

| 数据类型 | 推荐做法 |
| --- | --- |
| 密码、令牌、验证码 | 禁止记录 |
| 手机号、邮箱 | 掩码或哈希 |
| 用户身份 | 优先记录内部 `user_id` |
| 请求体 | 默认不记录，只白名单提取字段 |
| 异常堆栈 | 记录到错误日志，避免夹带敏感参数 |

更可靠的策略是白名单：允许哪些字段进入日志，而不是事后用正则到处替换。正则脱敏可以作为兜底，但不要把它当成唯一防线。

```js
function loggableUser(user) {
  return {
    user_id: user.id,
    role: user.role,
    email_hash: sha256(user.email.trim().toLowerCase())
  };
}
```

## 级别和采样要服务排障

`info` 适合记录关键业务节点，例如创建订单、支付回调、任务完成；`warn` 适合记录可恢复但异常的情况，例如下游短暂超时后重试成功；`error` 应该留给需要介入或会影响结果的问题。不要把所有异常都打成 `error`，也不要把每个循环细节都写成 `info`。

高频成功日志可以采样，例如只记录 1% 的健康检查、缓存命中或轮询结果；失败日志、慢请求日志和安全相关日志不要随意采样。采样字段也要写清楚，例如 `sample_rate: 0.01`，否则统计时容易误判规模。

## 落地清单

上线前可以用这份清单检查日志质量：

1. 日志输出为 JSON 或等价结构，不依赖纯文本解析。
2. 每条关键日志都包含 `event`、`level`、`service`、`trace_id`。
3. 入口、下游调用和异步任务都能传递 Trace ID。
4. 错误日志包含稳定的 `error_code`，不要只写异常字符串。
5. 敏感字段采用白名单记录，禁止令牌和密码落盘。
6. 成功高频日志有采样策略，失败和慢请求保留完整上下文。
7. 查询面板预置常用过滤条件，例如按 `trace_id`、`user_id`、`event`、`error_code` 搜索。

日志不是事后补丁，而是系统接口的一部分。只要字段稳定、链路不断、敏感信息不落盘，排障时就能少一些猜测，多一些证据。
