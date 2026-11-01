---
title: 用 OpenTelemetry 串起分布式追踪：Trace Context、Span 与采样治理
date: 2026-11-02 03:02:41
tags:
  - OpenTelemetry
  - 分布式系统
  - 可观测性
categories:
  - 工程实践
---

![分布式追踪把多个服务串成一条请求路径](/images/opentelemetry-distributed-tracing-basics/cover.jpeg)

一次请求经过网关、订单服务和库存服务后才返回。日志散落在多台机器上，只看时间戳很难定位慢点。分布式追踪给请求一个可传播的上下文，再把每段工作记录成有父子关系的 Span，最终还原完整路径。

<!-- more -->

## Trace、Span 与上下文

Trace 表示一次端到端请求，由 `trace_id` 标识。Span 是其中一段工作，例如解析 HTTP、查询数据库或调用下游服务。Span 保存 `span_id`、父 Span、开始与结束时间，并通过父子关系组成树。

上下文（Context）负责跨进程传递当前 Span。常见的 W3C Trace Context 使用 `traceparent` 携带版本、Trace ID、Span ID 和采样标志，`tracestate` 携带扩展信息。服务收到请求后提取上下文，创建新的服务端 Span，再把新上下文注入下游请求。

![Trace Context 沿着网关和微服务链路传播](/images/opentelemetry-distributed-tracing-basics/trace-context.jpeg)

## 在业务边界创建 Span

自动探针能覆盖常见 HTTP 和数据库调用，但关键业务仍应手动标记。伪代码的核心是“创建、记录异常、结束”：

```ts
const tracer = telemetry.getTracer("checkout-service");

async function createOrder(req) {
  return tracer.startActiveSpan("create-order", async (span) => {
    span.setAttribute("order.type", req.type);
    try {
      const order = await repository.insert(req.body);
      span.setAttribute("order.id", order.id);
      return order;
    } catch (error) {
      span.recordException(error);
      span.setStatus({ code: "ERROR" });
      throw error;
    } finally {
      span.end();
    }
  });
}
```

属性应使用路由模板、租户类型、结果状态等稳定维度。不要把邮箱、令牌、完整请求体或随机 ID 直接写入 Span：它们会泄露隐私，也会制造高基数索引。数据库参数应脱敏。

## 跨服务传播检查表

![采样管道把大量事件收敛为可分析的追踪数据](/images/opentelemetry-distributed-tracing-basics/sampling.jpeg)

| 边界 | 需要确认的事实 |
| --- | --- |
| 入站请求 | 提取 `traceparent`；格式错误时安全地新建 Trace |
| 出站请求 | 将当前上下文注入真正发送的请求对象 |
| 异步任务 | 把上下文写入队列消息，不依赖线程局部变量 |
| 线程池/协程 | 切换执行器后仍能恢复当前 Span |
| 非 HTTP 协议 | 消息头定义等价的 Trace ID、Span ID 字段 |

排查链路断裂时，按这些边界逐段验证。`trace_id` 只用于关联观测数据，不能充当权限凭证，也不能直接拼进 SQL 或文件名。

## 采样与导出治理

全部保留 Span 的成本很高。头部采样在入口决定是否记录；尾部采样等整条 Trace 完成后，再保留错误、慢请求或关键业务。采样决定应沿链路传播，避免上游保留、下游丢失。

可先设固定比例，再增加规则：错误请求 100% 保留，超过延迟阈值的请求提高比例，健康检查降低比例。压测时估算 Span 数量、导出带宽和存储，并监控导出队列丢弃数。导出端不可用时，业务请求应异步降级，不能因观测系统故障而阻塞主流程。

## 落地清单

1. 统一服务名、环境和版本字段。
2. 先接入口、出口、数据库、消息队列，再补关键业务 Span。
3. 用真实请求验证 `trace_id` 能穿过网关、服务和异步消费者。
4. 限制属性基数与敏感字段，监控错误、慢请求和采样率。
5. 演练上下文缺失、采样配置错误和导出端不可用。

每个边界都能说明上下文从哪里来、到哪里去、何时结束，追踪数据才会成为可靠的排障证据。
