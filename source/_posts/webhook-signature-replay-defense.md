---
title: 把 Webhook 接稳：签名校验、重放防护与可追溯处理
date: 2026-07-30 03:05:30
tags:
  - Webhook
  - API 安全
  - 后端
  - 可靠性
categories:
  - 工程实践
---

![Webhook 事件安全接收流程](/images/webhook-signature-replay-defense/cover.jpeg)

Webhook 把“定时去问对方有没有变化”变成“变化发生时由对方通知我”，很适合支付结果、代码仓库事件和订阅状态同步。但它的入口暴露在公网，收到一段看似正确的 JSON 并不等于事件可信：攻击者可以伪造请求，网络也会带来重复投递和乱序。要把 Webhook 接稳，应把验真、限时、去重和异步处理视为同一条防线。

<!-- more -->

## 先明确谁在证明什么

Webhook 的关键不是“请求来自某个 IP”，而是“请求体确实由持有共享密钥的一方在很短时间内签发”。多数提供方会把时间戳和签名放在 HTTP 头中；接收方使用同一密钥，对**原始字节**重新计算 HMAC，再进行常量时间比较。

| 校验项 | 解决的问题 | 常见误区 |
| --- | --- | --- |
| 签名 | 阻止任意伪造事件 | 只检查自定义 Header 是否存在 |
| 时间戳窗口 | 降低旧请求被重放的价值 | 只验签，不限制事件新鲜度 |
| 事件 ID | 消除正常重投和并发重复 | 用请求到达时间当唯一键 |
| 事件类型与版本 | 防止未知格式进入业务逻辑 | 反序列化后直接分支处理 |

密钥要按来源隔离，并放在密钥管理或受控环境变量中；不要把它写进仓库、日志或错误响应。来源 IP 白名单可以作为额外信号，却不应代替签名：云服务出口地址可能变化，代理链也会让地址判断变得脆弱。

## 验签前不要改写请求体

![原始请求体参与签名校验](/images/webhook-signature-replay-defense/raw-body.jpeg)

签名通常覆盖 `timestamp + "." + rawBody`。这里的 `rawBody` 是 HTTP 收到的原始字节，而不是 JSON 解析后再 `stringify` 的对象。空格、字段顺序、Unicode 转义都可能使两段语义相同的 JSON 生成不同字节；先经过通用 JSON 中间件再验签，很容易得到稳定的“签名不匹配”。

下面以 Node.js 风格伪代码说明顺序。真实项目应按提供方文档确认头名、拼接格式和编码方式：

```js
import crypto from "node:crypto";

function verifyWebhook(rawBody, signature, timestamp, secret) {
  const now = Math.floor(Date.now() / 1000);
  if (!Number.isInteger(Number(timestamp)) || Math.abs(now - Number(timestamp)) > 300) {
    throw new Error("stale webhook");
  }

  const payload = Buffer.concat([Buffer.from(`${timestamp}.`), rawBody]);
  const expected = crypto.createHmac("sha256", secret).update(payload).digest("hex");
  const received = Buffer.from(signature, "hex");
  const calculated = Buffer.from(expected, "hex");

  if (received.length !== calculated.length || !crypto.timingSafeEqual(received, calculated)) {
    throw new Error("invalid signature");
  }
}
```

先比较长度是为了避免比较函数因长度不同抛错；随后用常量时间比较，避免把逐字节比较的时间差变成猜测签名的旁路。校验失败时只返回通用的 400 或 401，并在内部日志记录来源、请求 ID 和失败类别，切勿记录完整密钥或敏感载荷。

## 把“至少一次投递”设计成常态

![快速确认与异步去重处理](/images/webhook-signature-replay-defense/dedup-queue.jpeg)

可靠的提供方会在超时或非成功响应后重试，因此同一事件到达两次并不表示攻击。接收端应该在验签后提取提供方的事件 ID，以它建立唯一约束；插入成功才投递后台任务，冲突则说明已受理，可以安全返回成功。不要以业务对象 ID 去重，因为一次订单可能产生创建、更新、退款等多个合法事件。

```sql
CREATE TABLE webhook_events (
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'received',
  PRIMARY KEY (provider, event_id)
);
```

推荐的处理链路是：读取原始体并验签 → 检查时间窗口和允许的事件类型 → 在短事务中写入事件记录与待发送任务 → 立即返回 `2xx` → 由工作进程执行耗时业务。这样既不会让第三方因处理过慢而重投，也能把失败任务重试、告警和人工回放从 HTTP 请求中剥离。若需要同时写库和发消息，可采用 outbox 表，让后台发布者可靠地发送已提交的任务。

## 接受乱序，并留下排障证据

重试和网络延迟意味着“后发生的事件先到”是正常现象。涉及状态迁移时，不能简单按接收顺序覆盖：应携带或查询业务版本、事件发生时间和允许的状态图。例如付款成功后又收到较早的待支付事件，较早事件不应把订单退回。无法判定顺序时，把事件保存为待核对并向权威 API 查询当前状态，比猜测更安全。

| 观测项 | 用途 |
| --- | --- |
| 签名失败数与来源 | 发现密钥配置错误或探测流量 |
| 事件延迟 | 判断时间窗口与队列是否合理 |
| 去重命中率 | 区分提供方重试与内部重复投递 |
| 处理成功率、重试次数、死信数 | 发现下游依赖或业务规则故障 |

日志至少关联 `provider`、`event_id`、事件类型、签名结果和处理状态；对载荷实行脱敏或只保存必要字段。上线前还应准备三类测试：合法请求、篡改一个字节后的请求，以及同一事件并发发送两次。再补一条超出时间窗口的请求，才能验证“验签通过但仍拒绝重放”的边界。

## 一份上线前清单

- 验签使用原始请求字节，签名比较采用常量时间函数。
- 时间戳有明确容忍窗口，服务器时钟保持同步。
- `(provider, event_id)` 有数据库唯一约束，而非仅靠内存缓存。
- HTTP 入口只做轻量校验和入队，耗时逻辑放到可重试的后台任务。
- 未识别事件默认记录并拒绝处理；密钥可轮换，旧密钥有短暂兼容期。

Webhook 的目标不是“每个请求只来一次”，而是让伪造进不来、重复不产生副作用、延迟和乱序仍能追溯。把这些边界变成代码、数据库约束和监控指标，第三方事件才会成为可靠的系统输入，而不是难以排查的公网后门。
