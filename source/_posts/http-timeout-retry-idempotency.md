---
title: 把 HTTP 接口做稳：超时、重试与幂等的工程清单
date: 2026-06-20 03:04:20
tags:
  - HTTP
  - API
  - 后端
  - 可靠性
categories:
  - 工程实践
---

![把 HTTP 接口做稳](/images/http-timeout-retry-idempotency/cover.jpeg)

很多接口事故并不是因为业务逻辑复杂，而是因为一次慢查询、一次网络抖动、一次客户端重复点击，把原本正常的链路拖进了未知状态。HTTP 调用要做稳，不能只靠“失败再试一次”。更可靠的做法，是把超时、重试和幂等放在一起设计：超时负责止损，重试负责修复短暂失败，幂等负责保证重复请求不会产生重复副作用。

<!-- more -->

## 先分清三件事

这三件事经常被混在一个配置里，但它们解决的问题不同。

| 机制 | 主要目标 | 常见错误 |
| --- | --- | --- |
| 超时 | 限制单次调用占用的最长时间 | 每一层都设置很长，导致请求堆积 |
| 重试 | 覆盖偶发网络错误或临时服务不可用 | 对所有错误无脑重试，放大流量 |
| 幂等 | 让重复写入只产生一次业务结果 | 只在客户端防重复点击，服务端没有约束 |

如果只配重试、不配合理超时，请求可能在链路里越积越多；如果只配超时、不做幂等，客户端重新发起写请求时又可能产生重复订单、重复扣款或重复消息。因此，稳态设计要从整条调用链开始，而不是只盯着某个 SDK 的默认参数。

## 超时按链路预算拆分

![请求链路的超时预算](/images/http-timeout-retry-idempotency/deadline.jpeg)

不要给客户端、网关、业务服务、数据库都写一个孤立的 `30s`。更好的方式是先定义用户可接受的总等待时间，再向下游逐层分配预算，并把剩余时间传递下去。

假设一个下单接口希望 1.5 秒内返回，可以这样拆：

| 阶段 | 建议预算 |
| --- | --- |
| 客户端到网关 | 200ms |
| 网关到订单服务 | 900ms |
| 订单服务内部处理 | 300ms |
| 预留网络与排队抖动 | 100ms |

伪代码可以把“截止时间”当成上下文传递，而不是每一层重新开始计时：

```js
function remainingMs(deadline) {
  return Math.max(0, deadline - Date.now());
}

async function createOrder(payload) {
  const deadline = Date.now() + 1500;

  return await http.post("/orders", payload, {
    timeout: remainingMs(deadline),
    headers: { "x-request-deadline": String(deadline) }
  });
}
```

服务端收到 `x-request-deadline` 后，也应该检查剩余时间。如果只剩几十毫秒，就不要再启动昂贵查询或调用更多下游服务，直接返回可理解的失败结果更可控。

## 重试只处理可恢复失败

重试不是“失败处理”的代名词。它只适合短暂、可能自愈的问题，例如连接被重置、网关临时超时、限流后允许稍后再试、下游短时间不可用。参数错误、权限错误、资源不存在这类确定性失败，重试只会浪费资源。

一个保守的客户端策略通常包含三点：

1. 限制最大次数，例如最多 2 到 3 次。
2. 使用指数退避，避免所有客户端同时重试。
3. 加入随机抖动，减少重试流量尖峰。

```js
async function requestWithRetry(send, maxAttempts = 3) {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      return await send();
    } catch (err) {
      if (!isRetryable(err) || attempt === maxAttempts - 1) throw err;

      const base = 100 * 2 ** attempt;
      const jitter = Math.floor(Math.random() * 80);
      await sleep(Math.min(base + jitter, 800));
    }
  }
}
```

服务端也要配合保护自己：对相同用户、相同接口、相同资源设置限流；在熔断或降级期间返回明确状态；把重试次数、耗时和最终结果打进日志。否则排查问题时只能看到“请求变多了”，却不知道变多来自真实流量还是客户端重试。

## 写接口必须有幂等键

![重试与幂等键](/images/http-timeout-retry-idempotency/idempotency.jpeg)

读接口天然更容易重试，写接口则必须先设计幂等边界。常见做法是让客户端为每次业务操作生成一个 `Idempotency-Key`，服务端用它识别“同一次操作的重复请求”。

关键点不在于请求头名字，而在于服务端要把它落到强约束里。只把 key 放在内存缓存中，遇到进程重启或并发请求仍然可能穿透。更稳的方式是在数据库里建立唯一索引：

```sql
CREATE TABLE idempotency_records (
  idempotency_key TEXT PRIMARY KEY,
  request_hash TEXT NOT NULL,
  response_body TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

处理流程可以是：

1. 校验 `Idempotency-Key` 是否存在，缺失就拒绝高风险写操作。
2. 计算请求体摘要，避免同一个 key 被拿来提交不同内容。
3. 在事务中插入幂等记录和业务数据。
4. 如果唯一键冲突，就读取旧记录：内容一致则返回原结果，内容不一致则返回冲突错误。

这样即使客户端因为超时没有收到响应，再次提交同一个操作时，服务端也能返回同一份结果，而不是重新执行一次副作用。

## 一份落地检查表

上线前可以按下面的清单过一遍：

| 检查项 | 判断标准 |
| --- | --- |
| 总超时 | 是否小于用户和上游能接受的等待时间 |
| 分层预算 | 下游调用是否共享同一个截止时间 |
| 重试条件 | 是否只覆盖网络错误、超时、临时不可用 |
| 退避策略 | 是否有次数上限、指数退避和随机抖动 |
| 幂等约束 | 写操作是否有服务端唯一约束 |
| 可观测性 | 日志里是否能看到 request id、重试次数、幂等 key |

可靠的 HTTP 接口不是把每个参数调大，而是让失败尽早暴露、让短暂失败有限恢复、让重复请求不重复产生副作用。把这三件事作为接口契约的一部分写进设计文档和测试用例，系统在流量抖动时会更容易保持可预测。
