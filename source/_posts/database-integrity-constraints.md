---
title: 别把校验只写在应用层：用数据库约束守住数据不变量
date: 2026-08-31 03:02:31
tags:
  - SQL
  - 数据库
  - 数据建模
  - 数据完整性
categories:
  - 工程实践
---

![数据库约束守护数据完整性](/images/database-integrity-constraints/cover.jpeg)

接口已经检查了金额不能为负，为什么数据库里仍会出现脏数据？因为数据入口不只有当前服务：旧版本程序、后台脚本、批量导入和并发请求都可能绕过应用校验。真正不可破坏的业务不变量，应由最接近数据的数据库再守一道底线。

<!-- more -->

## 应用校验为什么不够

应用层适合提供友好的即时反馈，却无法天然约束所有写入者。更隐蔽的问题是“先查询、再写入”的竞态：两个请求同时确认用户名不存在，随后都执行插入。如果表上没有唯一约束，两次写入都可能成功。

数据库约束是在事务写入点统一执行的规则。无论写入来自哪个进程，都必须通过同一套检查。它不是应用校验的替代品，而是数据完整性的最后防线。

| 业务不变量 | 推荐约束 | 防止的问题 |
| --- | --- | --- |
| 字段必须有值 | `NOT NULL` | 遗漏必填数据 |
| 标识不能重复 | `UNIQUE` | 并发重复写入 |
| 数值或状态合法 | `CHECK` | 越界值、非法枚举 |
| 引用对象必须存在 | `FOREIGN KEY` | 孤儿记录 |

![应用校验与数据库约束组成的两层防线](/images/database-integrity-constraints/validation-layers.jpeg)

## 把业务语言翻译成表结构

下面以 PostgreSQL 订单表为例，把“订单号唯一、金额非负、用户必须存在、状态只能取指定值”直接写进定义：

```sql
CREATE TABLE orders (
  id           bigint PRIMARY KEY,
  user_id      bigint NOT NULL,
  order_no     varchar(32) NOT NULL UNIQUE,
  amount_cents bigint NOT NULL CHECK (amount_cents >= 0),
  status       varchar(16) NOT NULL
               CHECK (status IN ('pending', 'paid', 'cancelled')),
  created_at   timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE RESTRICT
);
```

约束应表达稳定事实，而不是频繁变化的流程。例如“金额不能为负”适合 `CHECK`；“新用户首周最多下五单”依赖时间窗口和产品策略，更适合服务逻辑。还要注意，`CHECK` 通常只拒绝结果为假的表达式，空值可能通过检查，因此必填列仍需显式声明 `NOT NULL`。

外键的删除行为也必须有意识地选择。核心交易数据通常适合 `RESTRICT`，避免误删父记录；只有子数据确实没有独立生命周期时才考虑 `CASCADE`。同时检查数据库是否为外键子列自动建索引，若没有，应按查询和删除路径补充索引。

## 让唯一约束收口并发竞态

不要把下面的检查当作唯一性保证：

```sql
SELECT 1 FROM users WHERE email = :email;
-- 没查到之后再 INSERT，两个事务可能同时越过这里
```

正确做法是保留应用层预检以改善体验，同时让 `UNIQUE` 决定最终结果。写入冲突时，应用捕获数据库提供的“唯一约束违反”错误码，将其转换为稳定的业务错误，例如 HTTP `409 Conflict`，而不是解析可能变化的错误文本。需要幂等创建时，也可以使用数据库支持的 `INSERT ... ON CONFLICT` 或等价语法，但要明确冲突后返回旧记录还是拒绝请求。

![并发写入由唯一约束在数据库中统一裁决](/images/database-integrity-constraints/concurrent-uniqueness.jpeg)

## 给存量表补约束

直接给大表加约束可能因历史脏数据失败，也可能持锁影响线上请求。上线前先用反向查询暴露问题：

```sql
SELECT order_no, COUNT(*)
FROM orders
GROUP BY order_no
HAVING COUNT(*) > 1;

SELECT id FROM orders
WHERE amount_cents < 0 OR user_id IS NULL;
```

稳妥流程是先统计违规数据，确定修复规则并回填，再阻止新脏数据进入，最后创建和验证约束。具体的在线建索引、延迟验证和锁行为依赖数据库实现，应在与生产规模接近的环境演练，并准备超时与回滚方案。

## 错误处理与测试要跟上

约束上线后，违规写入会从“悄悄污染数据”变成“明确失败”，调用方必须正确处理。为每条关键约束至少准备三类测试：正常值能写入、边界与空值被拒绝、两个并发事务无法制造重复记录。测试应断言约束名或结构化错误码，不要绑定数据库的自然语言报错。

最后保留职责分工：前端负责及时提示，服务层负责格式、权限与跨资源规则，数据库负责任何入口都不能破坏的不变量。三层各守自己的边界，数据质量才不会依赖“所有调用者永远写对”。
