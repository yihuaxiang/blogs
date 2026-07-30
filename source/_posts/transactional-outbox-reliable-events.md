---
title: 用 Transactional Outbox 把领域事件发布做稳
date: 2026-07-31 03:05:26
tags:
  - 架构设计
  - 数据库
  - 消息队列
  - 可靠性
categories:
  - 工程实践
---

![Transactional Outbox 可靠事件发布](/images/transactional-outbox-reliable-events/cover.jpeg)

创建订单后既要提交订单数据，又要通知库存、积分或搜索系统；这两件事若分两步完成，就会留下危险的中间态：数据库成功而消息没发出，或消息已发出而事务随后回滚。Transactional Outbox（事务外盒）把“待发布事件”与业务数据放进同一事务，让可靠性先落在最擅长提交的数据库里。

<!-- more -->

## 双写为什么容易丢事件

最直觉的写法是先更新数据库再发消息，失败时重试发送。问题在于进程可能恰好在两步之间崩溃：订单已存在，但没有任何地方记得还要通知下游。若先发消息再提交数据库，则消费者可能读到一笔最终不存在的订单。把数据库和消息代理放进分布式事务有时可行，却会增加耦合、运维成本与故障面。

Outbox 的核心取舍是：不要求两个系统同时提交，而是在业务事务内持久保存一条事件。事务成功时，业务事实和事件意图一起存在；事务失败时，两者一起消失。独立的 relay（转发器）随后读取事件并投递到消息代理。

![业务事务与 Outbox 转发流程](/images/transactional-outbox-reliable-events/commit-flow.jpeg)

## 表结构与写入边界

Outbox 至少需要稳定事件 ID、类型、负载、创建时间与投递状态。负载应包含消费者所需的业务标识和版本；不要把整行数据库记录不加筛选地复制出去。

```sql
CREATE TABLE outbox_events (
  id           UUID PRIMARY KEY,
  event_type   VARCHAR(100) NOT NULL,
  aggregate_id UUID NOT NULL,
  payload      JSON NOT NULL,
  created_at   TIMESTAMP NOT NULL,
  published_at TIMESTAMP NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  lease_owner  VARCHAR(100) NULL,
  lease_until  TIMESTAMP NULL
);

BEGIN;
INSERT INTO orders (id, customer_id, status) VALUES (:id, :customer, 'created');
INSERT INTO outbox_events (id, event_type, aggregate_id, payload, created_at)
VALUES (:event_id, 'order.created', :id, :payload, CURRENT_TIMESTAMP);
COMMIT;
```

这段代码的关键不在 SQL 的形状，而在两个 `INSERT` 由同一个数据库事务提交。事件 ID 必须由生产者生成并长期保留：它既是链路追踪键，也是消费者去重键。对于有顺序要求的同一聚合，可额外保存递增版本号；全局严格顺序通常既昂贵又没有必要。

## Relay 如何安全地反复工作

Relay 轮询未发布记录，或读取数据库变更日志（CDC），再将消息发往代理。轮询方案更容易从零落地；CDC 能减少查询压力，但引入日志权限、连接器与运维复杂度。无论哪种方案，发送成功后更新 `published_at` 都不是原子整体：进程可能已把消息交给代理，却在标记前崩溃。

因此 Relay 必须允许重发。常见实现是用短事务领取一批记录，防止多个实例重复抢占；不同数据库的锁语义不同，生产前应验证所用数据库的条件更新或行锁行为。

```sql
-- 示意：短事务领取，网络发送在提交后进行
UPDATE outbox_events
SET lease_owner = :worker, lease_until = :deadline, attempts = attempts + 1
WHERE id IN (
  SELECT id FROM outbox_events
  WHERE published_at IS NULL
    AND (lease_until IS NULL OR lease_until < CURRENT_TIMESTAMP)
  ORDER BY created_at LIMIT 100
  FOR UPDATE SKIP LOCKED
);
```

租约到期后其他实例可接手；发送成功才写入 `published_at`。`SKIP LOCKED` 不是所有数据库都支持，生产前应按所用数据库验证领取语句。发送失败采用指数退避，并把超过阈值的事件转入可观测的失败状态；不能只靠无限重试悄悄堆积。

![重试、重复投递与消费者去重](/images/transactional-outbox-reliable-events/retry-deduplication.jpeg)

## 把重复交给消费者处理

Outbox 通常保证的是**至少一次投递**，而不是魔法般的“恰好一次”。消费者应以事件 ID 做幂等：在本地事务中先插入已处理记录，再执行业务更新；若 ID 已存在，就安全地跳过。对外部副作用（邮件、支付、第三方接口）同样要传递幂等键，或再使用一层自己的 Outbox。

| 场景 | 期望行为 |
| --- | --- |
| Relay 发送后崩溃 | 允许再次发送，消费者跳过重复 ID |
| 消息乱序到达 | 用聚合版本检测旧事件或按键分区 |
| 下游暂时不可用 | 延迟重试，不删除未确认事件 |
| Payload 格式演进 | 保留 `event_type` 与版本，消费者兼容旧字段 |

不要在代理确认前删除 Outbox 记录，也不要把 `published_at` 当作永久审计的唯一依据。实际可保留已发布记录一段时间，配合归档或按时间分区清理；清理策略要晚于消费者可能重放和排障所需的窗口。

## 上线前的最小验证

从故障注入开始：分别在事务提交前、提交后发送前、发送后标记前终止进程。每次恢复后检查业务数据与事件意图是否一致，并确认下游副作用最多发生一次。再观察未发布数量、最老事件年龄、重试次数、失败事件数和端到端延迟。

Transactional Outbox 不是消息队列的替代品，而是一条清晰的责任边界：数据库负责不可丢失地记录“应该发生什么”，Relay 负责可重试地送达，消费者负责抵抗重复。把这三层各自做对，跨服务协作才不会依赖那条脆弱的“双写成功路径”。
