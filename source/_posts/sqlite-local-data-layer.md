---
title: 用 SQLite 做轻量本地数据层：WAL、事务、索引与迁移清单
date: 2026-06-17 01:12:13
tags:
  - SQLite
  - 数据库
  - 工程实践
  - 后端
categories:
  - 数据库
---

![用 SQLite 做轻量本地数据层](/images/sqlite-local-data-layer/cover.jpeg)

很多内部工具、桌面应用、边缘服务和个人项目，并不需要一上来就接 PostgreSQL 或 MySQL。SQLite 的优势不是“玩具级简单”，而是把数据库能力放进一个普通文件里：部署少、依赖少、备份直观。只要把连接初始化、事务、索引和迁移这几件事做扎实，它完全可以承担一个可靠的本地数据层。

<!-- more -->

## 先判断是否适合

SQLite 最适合“本机读写、单文件存储、并发写入不高”的场景，例如命令行工具的缓存、桌面软件的项目文件、小型 Web 服务的配置与队列、测试环境里的真实数据库替身。它不适合把同一个库文件放在网络文件系统上多人读写，也不适合大量服务实例同时抢写一个文件。

| 问题 | 推荐判断 |
| --- | --- |
| 写入是否特别密集 | 单写者模型下要谨慎评估 |
| 是否需要跨机器共享 | 优先选服务端数据库 |
| 数据是否跟随应用文件移动 | SQLite 很合适 |
| 是否希望测试环境接近真实 SQL | SQLite 很方便，但要留意方言差异 |

## 连接创建时先补齐默认项

![SQLite WAL 写入模型](/images/sqlite-local-data-layer/wal.jpeg)

不要把 SQLite 当成“打开文件就完事”。每个进程启动后，都应该集中执行一组初始化语句，让运行环境可预期：

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

`WAL` 让读写互不阻塞得更少，适合大多数应用型场景；`foreign_keys` 需要显式开启，避免外键约束只写在 schema 里却不生效；`busy_timeout` 可以让短暂写锁等待一会儿，而不是立刻报错。若你的业务对断电后一条事务的落盘强度要求极高，可以评估把 `synchronous` 调成 `FULL`，用一点性能换更保守的持久性。

## 所有写入都放进事务

SQLite 很快，但“每条语句自动提交一次”会让批量写入变慢，也会让中间状态暴露出来。写入多张表、更新计数器、插入明细记录时，应该显式包进事务：

```sql
BEGIN IMMEDIATE;

INSERT INTO notes(id, title, body, created_at)
VALUES (?, ?, ?, ?);

INSERT INTO note_events(note_id, event_type, created_at)
VALUES (?, 'created', ?);

COMMIT;
```

`BEGIN IMMEDIATE` 会尽早获取写锁，适合“我确定接下来要写”的路径；如果拿不到锁，失败也发生在事务开头，更容易重试。应用层要保证异常时执行 `ROLLBACK`，不要让连接停在半开的事务里。

## 索引跟着查询走，不跟着直觉走

索引不是越多越好。每个索引都会占空间，并拖慢写入。比较稳的做法是：先列出高频查询，再为 `WHERE`、`JOIN`、`ORDER BY` 的组合建索引。

```sql
CREATE INDEX idx_notes_workspace_updated
ON notes(workspace_id, updated_at DESC);
```

这类索引适合“按工作区筛选，并按更新时间倒序取最近记录”的列表页。如果查询只按 `updated_at` 排序，不带 `workspace_id` 条件，这个索引的收益就会明显不同。上线前可以用 `EXPLAIN QUERY PLAN` 看 SQLite 是否真的使用了预期索引。

## 迁移必须版本化

![SQLite 迁移与索引治理](/images/sqlite-local-data-layer/migration.jpeg)

最容易埋雷的做法，是在本地手工打开数据库改表。正确方式是让应用维护一张迁移记录表，每次启动时按版本执行还没跑过的 SQL：

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

ALTER TABLE notes ADD COLUMN archived_at TEXT;

INSERT INTO schema_migrations(version, applied_at)
VALUES (3, datetime('now'));
```

迁移脚本要尽量可重复检查：先确认版本未执行，再在事务里改 schema 和写入版本号。SQLite 支持的 `ALTER TABLE` 能力比服务端数据库少，复杂改表可以采用“建新表、复制数据、重命名”的方式，过程同样要放进事务并提前备份。

## 备份与运行目录也要设计

如果启用了 WAL，运行时可能同时存在 `.db`、`-wal`、`-shm` 三个文件。直接复制单个主库文件并不总是安全，生产逻辑里更推荐使用 SQLite 的在线备份 API，或在可停机窗口先做 checkpoint 再复制。应用目录也要分清：配置文件、缓存数据库、用户项目数据库最好放在不同位置，便于清理、同步和故障排查。

## 落地清单

- 连接创建后统一执行 `PRAGMA` 初始化。
- 写路径用显式事务，必要时使用 `BEGIN IMMEDIATE`。
- 索引从真实查询出发，用 `EXPLAIN QUERY PLAN` 验证。
- schema 变更通过迁移脚本管理，不手工改库。
- 备份时考虑 WAL 相关文件，避免只复制半份数据。

SQLite 的关键不是“轻”，而是“边界清楚”。当数据天然属于单机应用、写入并发可控、运维成本需要压低时，它能用很小的复杂度提供完整的 SQL、事务和持久化能力。
