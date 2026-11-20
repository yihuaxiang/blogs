---
title: PostgreSQL Advisory Lock 实战：用数据库协调跨进程任务
date: 2026-11-21 03:03:20
tags:
  - PostgreSQL
  - 并发控制
  - 数据库
categories:
  - 数据库
---

![PostgreSQL Advisory Lock 协调多个工作进程](/images/postgresql-advisory-lock/cover.jpeg)

多个进程需要“同一时间只有一个人做”的事情时，常见做法是建一张锁表，再配合唯一键和过期时间。PostgreSQL 自带的 Advisory Lock（咨询锁）提供了更直接的协作协议：应用约定一个整数键，数据库负责等待、互斥和连接断开后的清理。它不绑定某一行数据，适合定时任务、缓存刷新、迁移前置检查等场景。

<!-- more -->

## 先分清两种生命周期

咨询锁有两组函数。事务级锁在当前事务结束时自动释放；会话级锁会一直跟随数据库连接，直到显式释放或连接断开。

| 类型 | 获取函数 | 释放时机 | 适合场景 |
| --- | --- | --- | --- |
| 事务级 | `pg_advisory_xact_lock` | `COMMIT` 或 `ROLLBACK` | 一次事务内的短临界区 |
| 会话级 | `pg_advisory_lock` | `pg_advisory_unlock` 或断开 | 跨多个事务的长任务 |

连接池会复用连接，因此会话级锁必须配对释放。

```sql
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('billing:daily', 0));
-- 读取并更新当天的汇总数据
COMMIT; -- 自动释放
```

键可以是一个 `bigint`，也可以是两个 `int`。不要直接把未经约束的字符串截断成整数，否则不同名称可能碰撞。`hashtextextended` 适合把稳定的业务名称映射成 64 位键；如果跨服务实现，也要固定哈希算法和种子。

## 等待、尝试与超时

阻塞式函数会一直等待，直到锁可用。对于 HTTP 请求或短生命周期的 Worker，这种等待会吞掉连接池和请求超时，通常应该使用非阻塞版本：

```sql
SELECT pg_try_advisory_xact_lock(hashtextextended('report:2026-11-21', 0));
```

返回 `true` 表示本次事务拿到锁，`false` 表示已有执行者。应用可以把 `false` 转成“跳过本轮”或排队信号，而不是返回 500。若必须等待，给事务设置明确的 `lock_timeout`，并为调用设置比它更长的应用超时：

```sql
SET LOCAL lock_timeout = '2s';
SELECT pg_advisory_xact_lock(9001);
```

![事务级与会话级锁的生命周期](/images/postgresql-advisory-lock/lock-lifecycle.jpeg)

## 把锁当作协作信号，而不是租约

咨询锁只存在于 PostgreSQL 内存中，能保证互斥，却不会记录任务进度或过期时间。需要重试、人工恢复的长任务，应同时维护任务表：状态写入数据库，咨询锁只负责缩小并发窗口。

典型流程是：事务内尝试获取任务键；失败就提交空事务并记录跳过原因；成功后检查任务状态，执行更新并写入完成标记，最后提交自动释放锁。键名应包含租户、资源类型和标识，例如 `invoice:{tenant_id}:{month}`，并记录计算出的键。

![非阻塞尝试锁、队列与超时](/images/postgresql-advisory-lock/try-lock.jpeg)

## 线上检查清单

- 默认选事务级锁，避免连接池残留状态。
- 为阻塞式等待设置 `lock_timeout`，并监控等待时长。

咨询锁把“谁先做”交给数据库原子决定。明确生命周期、键空间和失败路径后，它就能支撑定时任务和资源协调。
