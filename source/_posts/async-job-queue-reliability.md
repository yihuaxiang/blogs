---
title: 把异步任务跑稳：队列、幂等消费者与死信治理清单
date: 2026-07-03 03:04:30
tags:
  - 异步任务
  - 消息队列
  - 后端
  - 可靠性
categories:
  - 工程实践
---

![异步任务可靠性设计](/images/async-job-queue-reliability/cover.jpeg)

很多系统一开始把耗时逻辑丢进队列，只是为了让接口更快返回。等业务量上来后，真正难处理的往往不是“怎么入队”，而是任务重复执行、卡在半路、持续失败、人工不知道该不该重放。异步任务要跑稳，需要把队列、消费者、数据库状态和告警流程放在一起设计，而不是只换一个更强的消息中间件。

<!-- more -->

## 队列只解决一部分问题

队列擅长削峰、解耦和延迟处理，但它不会自动保证业务结果正确。网络闪断、进程重启、超时确认、重复投递都可能发生。多数成熟队列只能给出“至少投递一次”的基础保证，剩下的重复副作用、状态恢复和人工介入要由应用自己兜住。

| 风险 | 常见表现 | 应用侧要做什么 |
| --- | --- | --- |
| 重复投递 | 同一个任务被两个消费者处理 | 设计幂等键和唯一约束 |
| 半路失败 | 外部接口成功，本地状态没更新 | 用事务记录进度和结果 |
| 无限重试 | 坏数据反复占用 worker | 设置次数上限和死信队列 |
| 静默堆积 | 队列越来越长但没人注意 | 监控延迟、积压和失败率 |

判断一个任务系统是否可靠，不看它能不能把消息发出去，而看它在失败后能不能解释“这个业务动作现在处于什么状态，下一步可以由谁安全处理”。

## 明确任务生命周期

![任务生命周期](/images/async-job-queue-reliability/job-lifecycle.jpeg)

不要只把任务当成队列里的一条消息。更稳的做法是在业务数据库里保存一份任务状态，让队列负责调度，让数据库负责事实记录。一个通用状态机可以很简单：

| 状态 | 含义 |
| --- | --- |
| `pending` | 已创建，等待消费 |
| `running` | 已被某个 worker 领取 |
| `succeeded` | 业务副作用完成 |
| `failed` | 本次尝试失败，仍可重试 |
| `dead` | 超过上限，需要人工或补偿流程 |

任务表不需要复杂，但必须有业务唯一键、尝试次数和最近错误：

```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  business_key TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (kind, business_key)
);
```

`business_key` 用来表达“同一个业务动作”，例如 `order:123:send_receipt`。即使上游重复提交，数据库唯一约束也能让系统复用已有任务，而不是制造两次发信、两次扣库存或两次通知。

## 消费者先做幂等

异步任务最重要的默认假设是：同一条消息可能被处理多次。消费者代码要先检查业务结果，再执行副作用。对外部接口调用、数据库写入、文件生成、通知发送，都要有可查询的完成标记。

```js
async function handleSendReceipt(job) {
  return db.transaction(async (tx) => {
    const sent = await tx.receipts.findByJobId(job.id);
    if (sent) return { skipped: true };

    const order = await tx.orders.findById(job.payload.orderId);
    if (!order || order.status !== "paid") {
      throw new PermanentJobError("order is not ready");
    }

    await mailer.sendReceipt(order.email, order.id);
    await tx.receipts.insert({ jobId: job.id, orderId: order.id });
    await tx.jobs.markSucceeded(job.id);
  });
}
```

上面示例里，`receipts.job_id` 应该有唯一索引。真实项目中还要小心“外部调用成功但事务提交失败”的边界：如果外部服务支持幂等键，就把 `job.id` 传过去；如果不支持，就把本地发送记录提前写成 `sending`，重试时先核对外部状态或进入人工复核。

## 重试要有限、退避、分类型

失败不都值得重试。数据库短暂不可用、网络超时、第三方服务限流，通常可以重试；参数缺失、业务状态不允许、资源不存在，继续重试只会占用队列。

| 错误类型 | 处理方式 |
| --- | --- |
| 临时错误 | 指数退避后重试 |
| 永久错误 | 直接标记失败或进入死信 |
| 未知错误 | 少量重试，保留完整上下文 |

重试策略至少包含三个参数：最大次数、下次执行时间、随机抖动。不要失败后立刻把任务塞回队头，否则下游一抖动，所有 worker 会同时重放同一批坏任务。更稳的策略是按 `attempts` 拉长间隔，例如 30 秒、2 分钟、10 分钟，并为每次延迟加一点随机值。

还要避免任务被永久占住。worker 领取任务时记录 `locked_by` 和 `locked_until`，处理时间较长就定期续租；超过租约仍未完成的任务可以被其他 worker 接管。这样进程崩溃后，任务不会卡在 `running`。

## 死信队列不是垃圾桶

![死信与告警](/images/async-job-queue-reliability/dead-letter-alerts.jpeg)

死信队列的目的不是把失败藏起来，而是把“自动系统已经处理不了”的任务集中暴露出来。进入死信时要保留 `job_id`、任务类型、业务键、尝试次数、最后错误、关键 payload 摘要和关联日志 ID，方便排查。

一个可维护的死信流程应当支持三件事：查看失败原因、修正外部条件后重放、确认无法处理后归档。重放操作必须仍然走同一套幂等消费者，不能写一个绕过校验的“管理员补偿脚本”。否则事故修复脚本会成为新的事故来源。

## 上线检查表

| 检查项 | 判断标准 |
| --- | --- |
| 入队幂等 | 同一业务动作重复提交只生成一个任务 |
| 消费幂等 | 同一消息重复处理不会产生重复副作用 |
| 状态可查 | 能按业务键查到任务当前状态和最近错误 |
| 重试可控 | 有上限、退避、抖动和错误分类 |
| 死信可处理 | 有告警、查看、重放和归档入口 |
| 监控完整 | 看得到队列积压、最老任务年龄、成功率和失败率 |

异步任务的价值是把耗时工作从请求链路里移走，但可靠性不能也一起移走。把任务生命周期写进数据库，把消费者写成可重复执行，把失败路径纳入告警和操作流程，队列才会从“看起来更快”变成“失败时也可控”的工程能力。
