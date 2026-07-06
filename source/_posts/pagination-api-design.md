---
title: 把分页接口设计稳：Offset、Cursor、排序与一致性清单
date: 2026-07-07 03:05:49
tags:
  - API
  - 后端
  - 数据库
  - 性能
categories:
  - 工程实践
---

![把分页接口设计稳](/images/pagination-api-design/cover.jpeg)

分页接口很容易被当成 `limit + offset` 的小功能，直到列表翻到后面越来越慢，用户刷新后看到重复数据，或者运营导出时少了几行。分页真正要设计的是一组边界：结果如何排序，下一页从哪里开始，数据变化时是否允许漂移，以及客户端能不能理解“还有没有更多”。

<!-- more -->

## 先按场景选模型

不同列表对分页的要求并不一样，先分清场景可以少走很多弯路。

| 场景 | 更适合 | 原因 |
| --- | --- | --- |
| 后台管理页 | Offset | 需要跳页、总数和筛选组合 |
| 信息流、订单流水 | Cursor | 只向前或向后浏览，数据持续新增 |
| 大批量导出 | Cursor 或快照 | 要避免深分页和结果漂移 |
| 小表配置项 | Offset | 数据量小，交互简单 |

Offset 的优势是直观，客户端传 `page=3&page_size=20` 就能理解；Cursor 的优势是稳定，客户端只关心上一次返回的边界。不要为了“接口统一”把所有列表都做成一种模型。

## Offset 的两个隐患

Offset 最大的问题不是语法，而是它描述的是“跳过多少行”。当过滤条件命中很多数据时，数据库仍然要扫描并丢弃前面的记录，越翻越慢。

```sql
SELECT id, created_at, title
FROM articles
WHERE status = 'published'
ORDER BY created_at DESC, id DESC
LIMIT 20 OFFSET 20000;
```

第二个隐患是结果漂移。用户打开第一页后，如果前面插入了新数据，再请求第二页，原本第一页末尾的数据可能又出现在第二页；如果有数据被删除，也可能跳过一部分结果。Offset 可以用于数据量可控、允许跳页的后台页面，但不要把它用于无限滚动和高频写入的流水列表。

## Cursor 要绑定稳定排序

![游标边界与稳定排序](/images/pagination-api-design/cursor-boundary.jpeg)

Cursor 不是神秘 token，它本质上是“上一页最后一条记录的排序边界”。关键是排序必须稳定，不能只按 `created_at` 排，因为同一秒可能有多条记录。常见做法是用业务时间加唯一 ID 组成复合排序。

```sql
SELECT id, created_at, title
FROM articles
WHERE status = 'published'
  AND (created_at, id) < (:last_created_at, :last_id)
ORDER BY created_at DESC, id DESC
LIMIT :limit_plus_one;
```

实际接口可以多取一条来判断 `has_more`，返回给客户端时再裁掉多余记录。Cursor 内容可以是经过签名或编码的 JSON，例如包含 `created_at`、`id` 和必要的筛选条件摘要。不要把权限、租户 ID 或敏感字段裸露在可篡改的游标里。

```json
{
  "items": [],
  "next_cursor": "encoded-boundary",
  "has_more": true
}
```

## 处理翻页期间的数据变化

![翻页期间的数据一致性窗口](/images/pagination-api-design/consistency-window.jpeg)

分页期间数据一定可能变化，重点是提前定义可接受的语义。信息流通常允许看到最新数据，但要避免重复；订单流水、审计日志和导出任务更需要稳定窗口。

一种实用做法是在第一页确定上界，例如记录当时最大的 `created_at` 和 `id`，后续页面都加上 `<= first_seen_boundary`。这样新写入的数据会留到下一轮刷新，不会插入当前翻页过程。

```sql
WHERE status = 'published'
  AND (created_at, id) <= (:first_created_at, :first_id)
  AND (created_at, id) < (:last_created_at, :last_id)
```

如果业务要求“导出当下完整结果”，更稳的是创建后台任务，用同一份筛选条件和固定快照生成文件，而不是让浏览器一页页拉取。

## 接口约束要写清楚

分页接口至少要约定四件事：最大 `page_size`，排序字段是否固定，筛选条件变化时是否必须丢弃旧 cursor，以及 cursor 过期后的错误码。客户端拿旧 cursor 换了筛选条件，服务端应该直接拒绝，而不是返回看似正常但实际错位的数据。

落地时可以按这份清单检查：

- 列表是否真的需要跳页和总数。
- 排序是否包含唯一字段作为兜底。
- 深分页路径是否避开大 Offset。
- Cursor 是否绑定筛选条件和排序边界。
- 高频写入场景是否定义一致性窗口。
- 响应里是否明确返回 `has_more` 和 `next_cursor`。

分页接口的质量，取决于边界是否清楚，而不只是 SQL 写得短不短。先根据场景选择模型，再把排序、游标和变化语义写成接口契约，列表在数据量变大后才不容易变成性能和一致性问题。
