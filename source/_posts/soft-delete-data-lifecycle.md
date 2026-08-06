---
title: 软删除不是加个 deleted_at：把可恢复数据的生命周期做完整
date: 2026-08-06 03:07:47
tags:
  - 数据库
  - 数据建模
  - PostgreSQL
categories:
  - 工程实践
---

![软删除数据生命周期示意图](/images/soft-delete-data-lifecycle/cover.jpeg)

业务常把“删除”理解为暂时不让用户看见，于是给表加上 `deleted_at` 就上线。几个月后，重复注册被唯一索引拦住、后台统计混入已删除数据、关联记录变成孤儿，恢复操作又覆盖了新数据。软删除不是一个字段，而是一套从可见性、约束到最终清理的生命周期协议。

<!-- more -->

## 先决定：哪些数据真的值得恢复

软删除适用于误操作可撤销、需要保留审计线索，或删除后还要经过审批期的数据，例如项目、订单草稿和用户创建的内容。它不适合高频临时数据、明确受法定删除期限约束的敏感数据，也不能替代独立审计日志。

在建表前，把状态含义写成团队可执行的约定：`deleted_at IS NULL` 表示在线可见；非空表示逻辑删除；超过保留期后执行物理删除。仅有布尔型 `is_deleted` 通常不够，因为恢复、清理和排障都需要知道删除时间与操作者。

| 需求 | 建议字段或机制 | 容易遗漏的点 |
| --- | --- | --- |
| 默认隐藏 | `deleted_at` | 所有查询入口都必须应用条件 |
| 恢复与追责 | `deleted_by`、恢复事件 | 不要把操作者只写进应用日志 |
| 唯一名称复用 | 条件唯一索引 | 恢复时可能与新记录冲突 |
| 最终移除 | 清理任务与保留期 | 级联关系、备份与导出也要覆盖 |

## 用数据库约束表达“活跃记录唯一”

以邮箱唯一的用户表为例，普通唯一索引会让已删除账户仍占用邮箱。PostgreSQL 可以用部分唯一索引，只约束尚未删除的记录：

```sql
ALTER TABLE account
  ADD COLUMN deleted_at timestamptz,
  ADD COLUMN deleted_by bigint;

CREATE UNIQUE INDEX account_active_email_unique
  ON account (lower(email))
  WHERE deleted_at IS NULL;
```

现在旧账户删除后可重新注册；但恢复旧账户时，数据库会在存在新活跃账户时拒绝更新。这个失败是有价值的：它迫使产品明确策略——提示用户合并、改用新账户，或由管理员处理——而不是静默覆盖数据。

![应用查询边界与数据库条件唯一索引](/images/soft-delete-data-lifecycle/query-boundaries.jpeg)

索引必须与查询条件匹配。业务读取应始终带上 `deleted_at IS NULL`，并避免在每个调用点手写、手漏。可在仓储层提供默认作用域：

```python
def active_projects(conn, owner_id: int):
    return conn.execute(
        """
        SELECT id, name, updated_at
        FROM project
        WHERE owner_id = %s AND deleted_at IS NULL
        ORDER BY updated_at DESC
        """,
        (owner_id,),
    ).fetchall()
```

管理端的“含已删除”查询应是显式方法，而不是关闭全局过滤器后复用普通接口。对于 ORM，也要检查预加载、原生 SQL、报表、搜索索引和缓存失效路径；默认作用域只能减少遗漏，不能代替评审。

## 删除与恢复要看作状态转换

删除接口应是幂等的：对已删除记录再次请求，返回成功或清晰的当前状态，而非制造异常。更新时加上活跃条件，避免并发请求把已经恢复的记录再次删除：

```sql
UPDATE project
SET deleted_at = now(), deleted_by = :actor_id
WHERE id = :project_id
  AND deleted_at IS NULL;
```

关联关系必须提前定规则。若项目被删除，其任务是跟随隐藏、仍可独立访问，还是立即物理删除？不要只依赖外键的 `ON DELETE CASCADE`，因为软删除不会触发它。常见做法是把根实体和子实体放在同一事务中标记，或让所有子查询通过根实体的活跃状态过滤；两种方式都要有回归测试。

恢复同样需要验证父对象、唯一约束和权限。恢复后若缓存或搜索索引仍把记录当作不可见，用户会得到互相矛盾的结果；应把删除、恢复事件纳入同一套异步更新流程。

## 设置可观测的最终清理边界

![保留期结束后的批量清理与审计流程](/images/soft-delete-data-lifecycle/retention-cleanup.jpeg)

软删除会无限增大表、索引和备份，必须安排物理清理。清理作业以小批次处理已超过保留期的数据，记录候选数量、删除数量和失败原因，并允许按主键重试。先删依赖表还是先删根表，取决于外键与恢复语义；把顺序写成迁移和测试，而不是寄望运维临场判断。

```sql
DELETE FROM project
WHERE id IN (
  SELECT id
  FROM project
  WHERE deleted_at < now() - interval '30 days'
  ORDER BY id
  LIMIT 500
);
```

发布前用一份检查表收尾：活跃查询是否全覆盖，唯一约束是否允许复用，恢复冲突如何呈现，子记录如何处理，导出与搜索是否过滤，清理是否可监控和重试。这样，软删除才从“藏起一行数据”的临时补丁，变成可预测、可恢复且能最终收敛的数据生命周期。
