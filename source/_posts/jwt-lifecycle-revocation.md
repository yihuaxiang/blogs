---
title: JWT 不是会话：签发、轮换与撤销边界
date: 2026-10-30 03:02:21
tags:
  - JWT
  - Web 安全
  - 身份认证
categories:
  - 工程实践
---

![JWT 认证链路封面](/images/jwt-lifecycle-revocation/cover.jpeg)

把 JWT 放进 `Authorization` 头并不等于完成了登录系统。真正难处理的是令牌活多久、刷新时如何轮换、用户退出后怎样失效，以及密钥泄露时如何止损。把这些问题拆成生命周期，才能让“无状态”变成可运维的认证方案。

<!-- more -->

## 先分清三种状态

JWT 只是带签名的声明集合。服务端验证签名、`iss`、`aud`、`exp` 后，可以确认它由可信方签发且尚未过期，但这不表示用户当前仍有权限。权限变更、账号冻结、主动退出，都属于令牌之外的业务状态。

| 状态 | 保存位置 | 职责 |
| --- | --- | --- |
| Access Token | 客户端内存或安全 Cookie | 访问 API，寿命短 |
| Refresh Token | 服务端哈希记录 + 安全 Cookie | 换取新令牌，可轮换撤销 |
| 会话记录 | 数据库或 Redis | 绑定用户、设备和撤销时间 |

Access Token 是短期通行证，不能承担永久登录；Refresh Token 是长期凭证，要像密码一样保护和哈希存储。

## 签发：只放必要声明

Payload 不要塞入完整用户对象或敏感资料。声明越多，泄露后的影响越大，也越容易在权限变化后变成陈旧数据。一个可审计的最小集合如下：

```json
{
  "sub": "user_123", "sid": "session_8f2c",
  "iss": "https://auth.example.com", "aud": "api.example.com",
  "iat": 1761760941, "exp": 1761761841, "jti": "access_01J..."
}
```

验证端要固定允许的算法和发行方，不能根据 Token 的 `alg` 动态放宽规则；还要严格检查受众，避免发给 A 服务的令牌被 B 服务接受。Access Token 应有短 TTL，客户端遇到 401 只尝试一次刷新，避免并发请求把刷新端点打爆。

## 刷新：轮换比复用更易止损

推荐 Refresh Token Rotation：每次刷新都废弃旧令牌，生成新的一对，并把旧记录标记为已使用。旧令牌再次出现，说明它可能被复制，可以撤销同一 `sid` 下的全部刷新令牌。

![令牌轮换流程示意](/images/jwt-lifecycle-revocation/rotation.jpeg)

“消费旧令牌”和“写入新令牌”必须在同一事务或原子操作中完成：

```python
def rotate(token):
    r = store.find_by_hash(hash_token(token))
    if not r or r.revoked or r.expires_at <= now(): raise Unauthorized()
    if r.used_at:                 # 检测重放
        store.revoke_session(r.sid)
        raise Unauthorized()
    new = random_urlsafe(48)
    with store.transaction():
        store.mark_used(r.id, now())
        store.insert_refresh(r.sid, hash_token(new), now() + REFRESH_TTL)
    return issue_access(r.user_id, r.sid), new
```

并发刷新是常见坑：两个请求可能同时读到旧记录。数据库应使用行锁，或用 `UPDATE ... WHERE used_at IS NULL` 并检查受影响行数；Redis 场景则需要短时锁和幂等结果缓存。

## 撤销：无状态不等于无控制面

退出时删除客户端令牌，只能阻止当前设备继续发送，不能让已复制的 Access Token 立即失效。至少要支持单设备撤销、用户级全量撤销和密钥级紧急切换。

![撤销与审计控制台示意](/images/jwt-lifecycle-revocation/revocation.jpeg)

低成本方案是在会话记录中保存 `revoked_at`，每次刷新检查它；高风险接口可以额外查询会话状态。若要求立刻阻断所有请求，可在 Access Token 中加入短 TTL，或在 Redis 维护 `sid -> revoke_time`，验证 `iat` 是否早于撤销时间。不要把每个 JWT 都放进永久黑名单，否则无状态会退化成高成本会话表。

密钥轮换要有窗口：发布新 `kid` 后暂时保留旧密钥，等待旧令牌过期；确认没有旧流量后再删除。密钥泄露时则立即撤销相关会话并切换密钥。

## 上线前检查表

1. 固定算法、`iss`、`aud`，拒绝缺少 `exp` 的令牌。
2. 两类令牌使用不同用途和 TTL，Refresh Token 只在刷新端点出现。
3. Refresh Token 只存哈希，轮换原子化并能检测重放。
4. 支持按设备、按用户撤销，管理员操作写入审计日志。
5. 登录、刷新、退出接口限流，日志只记录 `jti`、`sid` 等不可逆标识。
6. 密钥带 `kid`，有轮换计划和紧急吊销预案。

JWT 的价值是让常规请求验证简单、便于横向扩展；会话、撤销和风险控制仍需要服务端边界。把令牌当作有期限的凭证，把刷新当作可审计的换证流程，认证系统才会在退出、改密和密钥事故中保持可控。
