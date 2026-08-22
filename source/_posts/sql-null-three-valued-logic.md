---
title: SQL NULL 不是一个值：三值逻辑、NOT IN 陷阱与数据建模
date: 2026-08-23 03:02:12
tags:
  - SQL
  - 数据库
  - 数据建模
  - 调试
categories:
  - 工程实践
---

![SQL NULL 与三值逻辑](/images/sql-null-three-valued-logic/cover.jpeg)

一条看似简单的筛选语句漏掉了数据，`NOT IN` 在测试库正常、上线后却返回空集，统计结果还总比总行数小——这些问题常常不是数据库算错了，而是代码把 `NULL` 当成了普通值。理解三值逻辑，再把“未知”纳入表结构与测试，才能让查询结果符合业务直觉。

<!-- more -->

## NULL 表示“未知”，不是一个值

`NULL` 可能表示尚未采集、不适用或暂时未知。因为它不是具体值，所以 `NULL = NULL`、`price <> NULL` 的结果都不是 `TRUE`，而是 `UNKNOWN`。`WHERE` 只保留结果为 `TRUE` 的行，`FALSE` 和 `UNKNOWN` 都会被过滤。

| 表达式 | 结果 |
| --- | --- |
| `10 = 10` | `TRUE` |
| `10 = NULL` | `UNKNOWN` |
| `NULL = NULL` | `UNKNOWN` |
| `NULL IS NULL` | `TRUE` |

![三值逻辑在查询过滤中的流向](/images/sql-null-three-valued-logic/three-valued-logic.jpeg)

因此，下面两条语句并不等价。第一条只返回已填写且不等于上海的记录；第二条才把未填写城市的记录也包含进来。

```sql
SELECT * FROM users WHERE city <> '上海';

SELECT *
FROM users
WHERE city <> '上海' OR city IS NULL;
```

判断空值必须使用 `IS NULL` 或 `IS NOT NULL`。若数据库支持 `IS DISTINCT FROM`，还可以用它进行包含空值的安全比较；跨数据库项目则应先确认具体方言。

## NOT IN 为何会被一个 NULL“污染”

假设封禁表里意外出现一条 `user_id = NULL` 的记录：

```sql
SELECT id
FROM users
WHERE id NOT IN (SELECT user_id FROM blocked_users);
```

`NOT IN` 可以理解为一组“不等于”判断的合取。任何候选 `id` 与 `NULL` 比较都会产生 `UNKNOWN`，最终条件无法成为 `TRUE`，查询可能一行也不返回。更稳妥的写法是使用相关子查询：

```sql
SELECT u.id
FROM users AS u
WHERE NOT EXISTS (
  SELECT 1
  FROM blocked_users AS b
  WHERE b.user_id = u.id
);
```

如果 `blocked_users.user_id` 在业务上必须存在，还应同时声明 `NOT NULL`，从数据入口消除歧义，而不是只修补每一条查询。

## 聚合函数也有自己的空值规则

`COUNT(*)` 统计行，`COUNT(column)` 只统计该列非空的行；`SUM`、`AVG`、`MIN`、`MAX` 通常也忽略空值。当没有可参与计算的值时，结果可能仍是 `NULL`。

```sql
SELECT
  COUNT(*) AS total_rows,
  COUNT(score) AS scored_rows,
  AVG(score) AS average_score
FROM reviews;
```

不要为了让报表“有数字”就随手写 `COALESCE(score, 0)`：未知评分和零分含义不同。应先由业务决定空值是否等价于零，再在查询的展示边界使用 `COALESCE`。

## 约束与唯一性要按数据库验证

![从业务语义到数据库约束的建模流程](/images/sql-null-three-valued-logic/null-modeling.jpeg)

`UNIQUE` 遇到 `NULL` 时的行为容易被误判，而且不同数据库、索引形式可能存在差异。需要“同一个邮箱不能关联多个用户”时，先回答邮箱能否缺失，再选择明确方案：必填就用 `NOT NULL UNIQUE`；允许缺失时，使用数据库支持的条件索引或等价约束，并为多个空值、重复值编写迁移测试。

更重要的是，不要让一个 `NULL` 同时表示“未填写”“不适用”和“已删除”。这些状态会影响筛选和统计时，应拆成明确字段，例如 `email_status`、`deleted_at`，或独立的状态表。字段语义越清楚，查询越少依赖猜测。

## 上线前的检查清单

- 为可空字段写清楚业务含义，能必填的字段直接加 `NOT NULL`。
- 查询条件同时准备非空、空值和边界值样本，不只测正常数据。
- 反连接优先考虑 `NOT EXISTS`，并检查关联列是否允许为空。
- 对比 `COUNT(*)` 与 `COUNT(column)`，确认报表分母是否正确。
- 在目标数据库上验证唯一约束和空值行为，不把方言差异留到生产环境。

处理 `NULL` 的核心不是记住几条特殊语法，而是承认“未知”会沿表达式传播。先定义字段为何可空，再用约束收紧入口，最后用包含空值的测试验证查询，三值逻辑就不会再成为隐藏故障。
