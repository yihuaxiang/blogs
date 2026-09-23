---
title: "JSON 部分更新怎么设计：Patch 与 Merge Patch 的语义、校验和并发控制"
date: 2026-09-24 03:03:04
tags:
  - API 设计
  - JSON
  - 并发控制
categories:
  - 工程实践
---

![JSON 部分更新 API 封面](/images/json-patch-api/cover.jpeg)

用户只改个人资料里的一个字段时，提交完整对象会增加网络开销和覆盖风险。部分更新 API 的关键，是把字段变化、数组处理和空值含义写成可执行的契约。本文比较 JSON Patch 与 JSON Merge Patch，并给出校验与并发控制方案。

<!-- more -->

## 两种格式解决不同问题

JSON Patch（RFC 6902）把更新表达为有序操作数组，每个操作通过 JSON Pointer 指向路径：

```json
[
  {"op": "test", "path": "/version", "value": 7},
  {"op": "replace", "path": "/displayName", "value": "Lin"},
  {"op": "add", "path": "/labels/-", "value": "backend"}
]
```

它能区分新增、删除、替换、移动和复制，适合数组元素和原子条件检查；`test` 失败时，整个文档不应被修改。

JSON Merge Patch（RFC 7396）则使用一个普通 JSON 对象描述目标片段：

```json
{"displayName": "Lin", "bio": null}
```

对象字段递归合并，`null` 表示删除，未出现的字段保持原值。它易于手写，但无法表达“替换数组第 2 项”；数组通常会整体替换。

![Patch 操作把文档变换为新状态](/images/json-patch-api/patch-operations.jpeg)

| 需求 | JSON Patch | JSON Merge Patch |
| --- | --- | --- |
| 修改数组单项 | 适合，路径可定位下标 | 通常只能整体替换 |
| 表达删除 | `remove`，语义明确 | `null`，需要允许字段为空的约定 |
| 客户端可读性 | 操作较多，适合工具生成 | 结构接近资源对象 |
| 条件更新 | `test` 可放入同一补丁 | 需配合 If-Match 或业务版本号 |

## 先做媒体类型和结构校验

不要根据请求体长相猜格式。服务端应要求 `Content-Type: application/json-patch+json` 或 `application/merge-patch+json`，否则返回 `415 Unsupported Media Type`。解析后做三层校验：

1. **语法层**：JSON 是否有效，Patch 是否为数组，操作名和必填字段是否齐全。
2. **路径层**：只允许公开字段，拒绝 `/roles`、`/ownerId` 等路径，并限制操作数、路径深度和请求体大小。
3. **领域层**：应用补丁后校验完整资源，例如邮箱格式、状态转换和字段不变量；校验放在写库事务内。

JSON Pointer 要正确处理 `~0`、`~1` 转义，不能把路径拼成 SQL 或文件路径。数组下标检查边界，`/-` 只能用于 `add`。错误响应固定包含 `code`、`path` 和 `message`，便于定位操作。

## 把读取和写入绑定起来

部分更新仍会遇到“最后一次写入覆盖前一次”。让资源携带版本号或 ETag，客户端提交条件请求：

```http
PATCH /users/42
Content-Type: application/json-patch+json
If-Match: "user-42-v7"

[{"op":"replace","path":"/displayName","value":"Lin"}]
```

服务端在事务中检查版本，应用补丁并递增版本；不匹配返回 `412 Precondition Failed`，不产生副作用。`test /version` 可作第二道保护，但不能替代数据库条件更新：`UPDATE ... WHERE id = ? AND version = ?`，并检查受影响行数。

![版本校验与锁共同保护并发更新](/images/json-patch-api/concurrency-validation.jpeg)

## 幂等、审计和上线清单

`replace` 同一个值通常幂等，`add /labels/-` 却会在重试时重复追加。可提供 `Idempotency-Key` 或保存操作编号做短期去重。审计记录操作者、媒体类型、原始补丁、旧版本和新版本，敏感字段脱敏。

上线前验证：删除不存在路径、数组越界、重复操作、空对象、超大补丁、权限不足、版本冲突和事务回滚。为两种媒体类型建立契约测试，确认错误码、响应 ETag 和重试行为稳定。这样，`PATCH` 才是可预测的更新协议。
