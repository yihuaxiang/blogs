---
title: 用 SQLite FTS5 构建可靠的本地全文检索
date: 2026-11-24 03:04:49
tags:
  - SQLite
  - 全文检索
  - 数据库
categories:
  - 工程实践
---

![SQLite FTS5 本地全文检索封面](/images/sqlite-fts5-search/cover.jpeg)

搜索框看似只是一个输入框，真正难的是让结果足够快、可解释，还能随着数据更新保持一致。SQLite 自带的 FTS5 扩展适合笔记、帮助文档、离线应用等中小规模场景：数据和索引放在同一个文件里，事务边界清楚，部署成本也低。

<!-- more -->

## FTS5 解决了什么问题

`LIKE '%关键字%'` 必须逐行扫描文本，数据量上来后延迟会随行数增长。FTS5 建立的是倒排索引：先把文本拆成词，再记录每个词出现在哪些行。查询时只需合并命中的 rowid，再按相关性排序。

![倒排索引结构示意](/images/sqlite-fts5-search/index.jpeg)

创建虚拟表时要先决定索引列和分词器：

```sql
CREATE VIRTUAL TABLE article_fts USING fts5(
  title, body,
  content='article',
  content_rowid='id',
  tokenize='unicode61'
);
```

这里的 `content='article'` 表示使用外部内容表。FTS5 只保存词项和 rowid，正文仍在业务表中，能减少重复存储；代价是业务表和索引必须一起维护。

## 外部内容表的同步边界

首次建索引可以用 `rebuild`：

```sql
INSERT INTO article_fts(article_fts) VALUES ('rebuild');
```

新增、修改、删除则应放进同一个事务。最简单的方式是用触发器同步：

```sql
CREATE TRIGGER article_ai AFTER INSERT ON article BEGIN
  INSERT INTO article_fts(rowid, title, body)
  VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER article_ad AFTER DELETE ON article BEGIN
  INSERT INTO article_fts(article_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
END;
```

更新操作要先删除旧索引内容，再插入新内容；若只插入新文本，旧词项可能残留，搜索结果会出现“幽灵命中”。批量导入时可以暂停触发器，导入完成后统一 `rebuild`，但这段窗口必须禁止对外查询，或者明确接受索引暂时不完整。

## 查询、排序与高亮

基础查询使用 `MATCH`，结果表通过 `rowid` 关联：

```sql
SELECT a.id, a.title, a.updated_at,
       bm25(article_fts) AS score
FROM article_fts
JOIN article AS a ON a.id = article_fts.rowid
WHERE article_fts MATCH :q
ORDER BY score
LIMIT :limit OFFSET :offset;
```

FTS5 的 `bm25` 数值越小通常代表相关性越高，因此排序方向不要写反。用户输入不能直接拼接 SQL；参数化只能防止注入，不能替你处理 FTS 查询语法。产品若只需要普通关键词，可以先按空白切词、转义双引号，并限制长度和词数。

![查询与排序流水线](/images/sqlite-fts5-search/query.jpeg)

需要展示命中片段时，可使用 `snippet` 或 `highlight`，但要在输出 HTML 前再次转义，避免把原文当作标签渲染。高亮结果属于展示数据，不应覆盖数据库里的原始正文。

## 规模和运维清单

| 场景 | 建议 |
| --- | --- |
| 小量写入、频繁查询 | 事务内同步 FTS，定期观察查询耗时 |
| 大批量导入 | 导入后统一 `rebuild`，再开放查询 |
| 索引膨胀 | 在低峰执行 `optimize`，并保留备份 |
| 中文搜索 | 评估分词效果；必要时在写入前生成业务词元 |
| 多进程访问 | 开启 WAL，设置合理 busy timeout，避免长事务 |

实际上线时还应记录查询耗时、命中数量和空结果比例，用真实数据校准分词和排序策略。索引更新失败要能告警并支持重建，避免问题直到用户反馈才被发现。

FTS5 不是通用搜索集群：它不提供分布式分片、复杂拼写纠错或跨机高可用。数据规模和并发超过单机 SQLite 的舒适区后，应把词法分析、索引更新和迁移成本纳入评估，再考虑专用搜索服务。对多数本地应用而言，先用清晰的事务同步、参数化查询和可重建索引，就能得到足够可靠的全文检索能力。
