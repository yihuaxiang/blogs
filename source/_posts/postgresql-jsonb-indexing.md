---
title: PostgreSQL JSONB 索引怎么选：GIN、表达式索引与查询形状
date: 2026-11-16 03:06:10
tags:
  - PostgreSQL
  - JSONB
  - 数据库性能
categories:
  - 数据库
---

![PostgreSQL JSONB 索引示意图](/images/postgresql-jsonb-indexing/cover.jpeg)

JSONB 让一张表可以容纳变化较快的业务字段，但“把整列建一个索引”并不等于所有查询都会变快。索引类型、操作符和查询写法必须匹配，才能在数据量上来后保持可预测的响应时间。

<!-- more -->

## 先分清三类查询

假设有一张订单表：

```sql
CREATE TABLE orders (
  id         bigint PRIMARY KEY,
  created_at timestamptz NOT NULL,
  attrs      jsonb NOT NULL DEFAULT '{}'
);
```

`attrs` 中保存渠道、设备和标签。常见查询可以分成三类：

| 查询形状 | 示例 | 更适合的索引 |
| --- | --- | --- |
| 包含关系 | `attrs @> '{"channel":"app"}'` | GIN |
| 键是否存在 | `attrs ? 'coupon'` | GIN |
| 取出一个标量比较 | `attrs->>'region' = 'east'` | 表达式 B-Tree |

包含关系强调“文档里是否包含这段结构”，而标量比较是先取值再做等值或范围判断。两者看起来都在查 JSON，优化器实际需要的索引结构完全不同。

## GIN：适合文档包含与键集合

最通用的起点是：

```sql
CREATE INDEX orders_attrs_gin
ON orders USING gin (attrs);
```

它能帮助 `@>`、`?`、`?|`、`?&` 等查询快速缩小候选集。写查询时尽量保留 JSONB 操作符的语义：

```sql
SELECT id
FROM orders
WHERE attrs @> '{"channel":"app", "paid":true}';
```

不要为了“看起来直观”改写成多次文本提取，除非你确实需要表达式索引。GIN 索引通常比 B-Tree 更大，写入也要维护更多索引项；字段内容高度重复、查询主要是单个标量等值时，整列 GIN 可能是过度设计。

## 表达式索引：把热点字段单独拿出来

如果业务最常筛选 `region`，可以只给这个路径建 B-Tree：

```sql
CREATE INDEX orders_region_idx
ON orders ((attrs->>'region'));

SELECT id
FROM orders
WHERE attrs->>'region' = 'east';
```

索引表达式必须和查询表达式一致。下面这种写法增加了类型转换，可能无法复用上面的索引：

```sql
WHERE (attrs->'region')::text = '"east"'
```

数值字段建议在表达式中明确转换，并在查询中保持同样的类型：

```sql
CREATE INDEX orders_score_idx
ON orders (((attrs->>'score')::integer));

SELECT id FROM orders
WHERE (attrs->>'score')::integer >= 80;
```

如果键不存在，转换可能失败。生产数据允许缺失时，先用约束、写入校验或安全的分层字段保证类型稳定，再考虑这种索引。

![JSONB 查询形状与索引路径](/images/postgresql-jsonb-indexing/query-shapes.jpeg)

## 多列索引不要从想象开始

常见误区是一次创建 `(created_at, (attrs->>'region'))`，然后期待所有组合查询都受益。多列 B-Tree 对左侧列的过滤顺序敏感，实际效果取决于选择性、排序和分页条件。先用真实查询测量：

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, created_at
FROM orders
WHERE attrs->>'region' = 'east'
ORDER BY created_at DESC
LIMIT 50;
```

观察是否使用了索引、扫描了多少行、读取了多少缓冲区。不要只看“Index Scan”几个字；如果估算行数严重偏小，可能需要更新统计信息，或者拆出高频字段。

## 维护成本同样要算

JSONB 内容越大，GIN 索引越容易膨胀。批量导入、频繁更新同一行的 JSON 字段时，写放大会更明显。上线前记录索引大小和典型查询耗时，发布后持续观察：

```sql
SELECT indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname = 'orders';
```

![索引维护与体积观察](/images/postgresql-jsonb-indexing/maintenance.jpeg)

索引不是越多越好。可以按下面顺序落地：

1. 列出前几条真实且稳定的 JSONB 查询，不凭字段数量猜索引。
2. 用 `EXPLAIN (ANALYZE, BUFFERS)` 保存基线。
3. 包含关系先试 GIN，单字段等值或范围先试表达式 B-Tree。
4. 对缺失键、类型错误和空值写数据约束或应用层校验。
5. 观察写入延迟、索引体积和回归查询，再决定是否保留。

JSONB 适合承载变化中的边缘字段，但核心筛选条件如果长期稳定，最终往往值得提升为普通列。让查询形状决定索引，而不是让索引反过来限制查询，系统会更容易维护。
