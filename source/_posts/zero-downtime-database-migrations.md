---
title: 把数据库迁移做稳：Expand/Contract、回填与回滚清单
date: 2026-07-10 03:05:42
tags:
  - 数据库
  - 后端
  - 可靠性
  - 工程实践
categories:
  - 数据库
---

![把数据库迁移做稳](/images/zero-downtime-database-migrations/cover.jpeg)

线上数据库迁移最怕的不是 `ALTER TABLE` 本身，而是应用版本、数据形态和回滚路径没有对齐。一次看似简单的改字段，可能同时影响旧代码读写、新代码灰度、历史数据回填、索引构建和报表查询。要把迁移做稳，关键是把“改表”拆成可观察、可暂停、可回滚的多个小步骤。

<!-- more -->

## 迁移风险来自兼容窗口

数据库不像普通代码，可以随发布包一起瞬间替换。表结构一旦改动，多个应用实例、后台任务、临时脚本和只读服务都会立刻看到新状态。真正危险的是兼容窗口：旧代码还在运行，新字段已经出现；新代码开始写入，历史数据还没补齐；迁移跑到一半，发布需要回滚。

常见风险可以先分层看：

| 风险 | 典型表现 | 更稳的处理 |
| --- | --- | --- |
| 破坏旧代码 | 删除列、改类型后旧实例报错 | 先兼容，最后清理 |
| 长事务锁表 | 大表 DDL 或批量更新拖慢写入 | 小步迁移，避开高峰 |
| 回填放大负载 | 扫全表、写热点索引 | 分批、限速、可暂停 |
| 数据不一致 | 双写漏字段或回填覆盖新值 | 加校验和幂等条件 |

迁移方案不要只写“执行 SQL”。它至少要说明发布顺序、失败时停在哪里、哪些指标异常必须中止，以及回滚后数据是否仍能被旧代码读取。

## 用 Expand/Contract 拆开改动

![Expand Contract 迁移阶段](/images/zero-downtime-database-migrations/expand-contract.jpeg)

Expand/Contract 是线上迁移里很实用的套路：先扩展结构，让新旧代码都能工作；再切流量和补数据；最后收缩旧结构。它牺牲一点过程长度，换来更明确的回滚空间。

假设要把 `users.name` 拆成更明确的 `display_name`，不要直接改名。第一步是新增可空列：

```sql
ALTER TABLE users ADD COLUMN display_name VARCHAR(120);
CREATE INDEX idx_users_display_name ON users(display_name);
```

第二步发布兼容代码。读路径优先读新列，没有值就回退旧列；写路径可以双写，确保新数据不再欠账：

```js
function userName(row) {
  return row.display_name || row.name;
}

async function updateProfile(userId, input) {
  await db.query(
    "UPDATE users SET name = ?, display_name = ? WHERE id = ?",
    [input.displayName, input.displayName, userId]
  );
}
```

第三步跑历史回填。等校验通过、所有实例都已使用新列，再进入 Contract 阶段，删除旧读逻辑和旧列。删除列应该放在最后一个独立发布里做，因为它最难回滚。

## 回填任务要能停下来

![回填任务与观测](/images/zero-downtime-database-migrations/backfill-observability.jpeg)

回填不是一次性脚本，而是一个临时但真实的生产任务。它应该有游标、有批次、有速率限制，并且重复执行不会破坏数据。尤其不要用一条不带边界的 `UPDATE users SET display_name = name WHERE display_name IS NULL` 去赌数据库扛得住。

一个更稳的伪代码如下：

```js
const batchSize = 500;

while (true) {
  const rows = await db.query(
    `SELECT id, name
       FROM users
      WHERE display_name IS NULL
        AND id > ?
      ORDER BY id
      LIMIT ?`,
    [cursor, batchSize]
  );
  if (rows.length === 0) break;

  for (const row of rows) {
    await db.query(
      `UPDATE users
          SET display_name = ?
        WHERE id = ?
          AND display_name IS NULL`,
      [row.name, row.id]
    );
  }

  cursor = rows[rows.length - 1].id;
  await sleep(200);
}
```

这里的 `display_name IS NULL` 是幂等保护：如果用户在回填期间刚好修改资料，新值不会被旧值覆盖。批次大小和暂停时间要跟线上指标联动，数据库延迟升高、复制延迟扩大、错误率上升时，任务应当能立刻暂停。

## 校验比“跑完了”更重要

迁移完成的定义不能只是脚本退出。更可靠的做法是准备一组校验查询，并把结果贴到发布记录里：

```sql
SELECT COUNT(*) AS missing_display_name
FROM users
WHERE display_name IS NULL;

SELECT COUNT(*) AS mismatch_count
FROM users
WHERE display_name IS NOT NULL
  AND name IS NOT NULL
  AND display_name <> name;
```

如果业务允许用户自定义新旧字段差异，第二条查询就要换成业务规则校验，而不是机械比较。校验要覆盖总量、抽样、异常分布和关键租户。对于高风险表，还可以临时记录双读结果，把旧逻辑和新逻辑的输出差异打到日志或指标里，等差异稳定为零后再删除旧路径。

## 回滚方案要按阶段写

回滚不是一句“回滚应用版本”。在 Expand 阶段，回滚通常简单，因为旧列还在，新列只是多余字段；在双写阶段，要确认旧代码是否能忽略新列；在 Contract 阶段，如果已经删除旧列，回滚成本会急剧上升。

可以按阶段写清楚：

| 阶段 | 可回滚动作 | 注意点 |
| --- | --- | --- |
| 新增列后 | 回滚应用，保留新列 | 不急着删无害字段 |
| 双写期间 | 关闭新读路径 | 保持旧列仍被写入 |
| 回填期间 | 暂停任务 | 游标和批次进度要持久化 |
| 删除旧列后 | 只能走恢复或反向迁移 | 需要单独审批 |

这张表的价值在于让团队提前承认边界：有些阶段是低风险可逆的，有些阶段是高风险单向门。越接近删除和类型收窄，越应该拆成单独发布，并安排更严格的观察窗口。

## 上线前的最小清单

最后可以用一份短清单收口：迁移 SQL 已在相似数据量上演练；应用代码能同时兼容新旧结构；回填任务可暂停、可重跑、有限速；校验 SQL 已准备；监控覆盖错误率、慢查询、复制延迟和锁等待；每个阶段都有明确负责人和停止条件。

数据库迁移做得稳，不是因为一次 SQL 写得漂亮，而是因为每一步都允许系统继续服务。把结构扩展、代码兼容、数据回填、校验清理拆开，线上变更就会从一次豪赌，变成一组可以被观察和控制的小动作。
