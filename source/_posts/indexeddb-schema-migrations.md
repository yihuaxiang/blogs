---
title: 浏览器本地数据库升级：IndexedDB 版本迁移与事务边界
date: 2026-11-15 03:03:37
tags:
  - JavaScript
  - IndexedDB
  - 浏览器存储
  - 数据迁移
categories:
  - 前端工程
---

![浏览器本地数据库在不同版本之间迁移](/images/indexeddb-schema-migrations/cover.jpeg)

IndexedDB 的难点不在保存一条记录，而在应用发布新版本后，旧用户如何安全升级表结构。把版本迁移、多个标签页和事务失败边界设计清楚，离线应用才能长期可恢复。

<!-- more -->

## 用版本号表达架构契约

`indexedDB.open(name, version)` 的版本号描述数据库架构。首次创建或版本号变大时触发 `onupgradeneeded`，对象仓库和索引都应在这里创建。

```js
const request = indexedDB.open('notes', 3);
request.onupgradeneeded = (event) => {
  const db = event.target.result;
  const old = event.oldVersion;
  if (old < 1) {
    const notes = db.createObjectStore('notes', { keyPath: 'id' });
    notes.createIndex('by_updated_at', 'updatedAt');
  }
  if (old < 2) db.createObjectStore('settings', { keyPath: 'key' });
  if (old < 3) {
    db.transaction.objectStore('notes').createIndex('by_deleted', 'deleted');
  }
};
```

迁移要写成连续步骤，而不是只处理“上一个版本”。用户可能从 1 直接跳到 3，因此每一步都用 `oldVersion < n` 判断，避免重复创建仓库或索引。

## 升级事务只做可控工作

`onupgradeneeded` 运行在版本变更事务中，可以创建仓库、删除索引和改写记录，但不能等待网络请求。批量转换要通过请求链保持事务活跃：

```js
const tx = db.transaction('notes', 'readwrite');
const store = tx.objectStore('notes');
const cursorRequest = store.openCursor();
cursorRequest.onsuccess = () => {
  const cursor = cursorRequest.result;
  if (!cursor) return;
  const value = cursor.value;
  value.deleted = Boolean(value.deleted);
  cursor.update(value);
  cursor.continue();
};
tx.onabort = () => reportMigrationFailure(tx.error);
```

不要在请求之间 `await` 一个无关的 Promise；事务可能已经提交，后续访问会抛出 `TransactionInactiveError`。只有 `tx.oncomplete` 到达，才算迁移成功。

![IndexedDB 版本迁移的连续步骤](/images/indexeddb-schema-migrations/migration-flow.jpeg)

## 处理多标签页阻塞

一个旧页面保持数据库连接时，新页面升级会触发 `blocked`。每次打开都监听 `versionchange`，收到通知后关闭连接并停止写入：

```js
request.onblocked = () => showToast('请关闭其他页面后重试');
db.onversionchange = () => {
  db.close();
  showToast('数据结构已更新，请刷新页面');
};
```

不要让每个标签页自动互相刷新，容易形成刷新风暴。关闭连接后提示用户刷新，或交给一个页面协调器统一处理。

![IndexedDB 事务提交与回滚边界](/images/indexeddb-schema-migrations/transaction-lifecycle.jpeg)

## 让失败可恢复

迁移事务失败会自动回滚，数据库保持旧结构。错误日志至少记录数据库名、旧版本、新版本和错误类型。索引字段可能缺失时，先写入默认值再建立索引；数据量很大时，可把转换拆成多个版本步骤。

| 场景 | 推荐处理 |
| --- | --- |
| 缺少对象仓库 | 在对应版本步骤中创建 |
| 旧标签页阻塞 | 监听 `versionchange` 并关闭连接 |
| 批量迁移失败 | 依赖回滚并记录诊断信息 |
| 不可恢复的结构冲突 | 明确提示后再删除并重建 |

## 发布前检查

准备空库、每个历史版本快照和跳版本升级测试。验证索引、记录数量、重复打开幂等性，以及刷新或断电后能否继续使用。IndexedDB 的可靠性来自清晰的版本契约和事务边界，而不是一个庞大的初始化函数。
