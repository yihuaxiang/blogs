---
title: 密码存储不是加密：盐、慢哈希与渐进迁移
date: 2026-10-19 03:01:38
tags:
  - 安全
  - 密码哈希
  - Python
  - 身份认证
categories:
  - 工程实践
---

![密码经过慢哈希后进入安全存储](/images/password-hashing-storage/cover.jpeg)

数据库泄露后，真正决定损失范围的不是密码表有没有改名，而是攻击者能否低成本地离线猜测。密码既不该明文保存，也不该用可解密的方式保存；更可靠的做法是为每个密码生成独立随机盐，再交给专门的慢哈希算法处理，只保存验证所需的参数与结果。

<!-- more -->

## 先分清三种能力

加密需要密钥，并且设计目标是以后还原原文；普通快速哈希适合校验文件，却也方便攻击者每秒尝试大量候选密码；密码哈希则故意消耗 CPU 和内存，提高每次猜测的成本。登录系统只需要回答“本次输入是否匹配”，没有取回原密码的业务需求。

| 方案 | 能否还原 | 典型用途 | 是否适合存密码 |
| --- | --- | --- | --- |
| 对称加密 | 持有密钥即可 | 需要读取的敏感数据 | 否，密钥泄露会暴露全部密码 |
| SHA-256 等快速哈希 | 否 | 文件完整性、内容寻址 | 否，离线猜测成本太低 |
| Argon2、scrypt、bcrypt、PBKDF2 | 否 | 口令派生与验证 | 是，参数需按环境评估 |

“多做几轮 SHA-256”仍不是理想替代：专用算法提供明确的成本参数，有些还能增加内存成本，更容易测量、升级和审计。

## 盐解决的是批量复用

![随机盐与慢哈希共同保护密码](/images/password-hashing-storage/hash-pipeline.jpeg)

盐是每条密码记录独立生成的随机字节，不需要保密。同一个密码配上不同盐，会得到不同结果，因此攻击者不能用一张预计算表同时匹配所有用户，也不能从哈希相同直接判断两人密码相同。

盐不能阻止针对单个用户的猜测，所以仍需慢哈希。另一个常被混淆的概念是 pepper：它是全站共享、存放在数据库之外的秘密。pepper 可以增加一道防线，但会引入轮换和灾难恢复问题，不能替代随机盐，也不应硬编码进仓库。

## 记录算法和参数，而不只记录摘要

密码哈希必须能够随硬件与风险变化而升级。一条可维护的记录至少包含算法标识、成本参数、盐和派生结果，例如：

```text
scrypt$n=16384,r=8,p=1$<base64-salt>$<base64-derived-key>
```

下面用 Python 标准库演示完整的生成与验证。参数只是便于运行的示例；生产环境应在部署规格上压测，让正常登录延迟可接受，同时限制并发，避免高成本计算反过来成为拒绝服务入口。

```python
import base64
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2**14, 8, 1, 32

def encode_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=N, r=R, p=P, dklen=DKLEN,
    )
    b64 = lambda value: base64.b64encode(value).decode("ascii")
    return f"scrypt$n={N},r={R},p={P}${b64(salt)}${b64(derived)}"

def verify_password(password: str, record: str) -> bool:
    scheme, params, salt_text, expected_text = record.split("$", 3)
    if scheme != "scrypt":
        return False
    values = dict(item.split("=") for item in params.split(","))
    salt = base64.b64decode(salt_text, validate=True)
    expected = base64.b64decode(expected_text, validate=True)
    actual = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=int(values["n"]), r=int(values["r"]), p=int(values["p"]),
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)
```

`compare_digest` 避免普通比较过早返回造成不必要的时序差异。解析记录时还应设置参数上限与格式校验，不能盲信数据库中的成本值，否则损坏或恶意数据可能触发超大内存分配。

## 用登录成功完成渐进迁移

![旧密码哈希逐步迁移到新方案](/images/password-hashing-storage/rehash-migration.jpeg)

升级算法不需要强制所有用户同时重置密码。验证函数先根据记录中的算法标识走旧逻辑；旧记录验证成功后，立即用当前方案重新计算并更新该用户记录。长期未登录的账户可以保留旧格式、限制高风险操作，或在明确期限后要求重置。

迁移时要避免两个坑：不要把旧哈希值当成新算法的“密码”再包一层，否则无法统一验证语义；并发登录更新时使用条件更新，只在记录仍等于刚验证的旧值时替换，避免覆盖用户同时发生的密码修改。

## 登录入口还需要外围防线

安全存储只能减轻数据库泄露后的风险，不能独自解决弱密码与在线撞库。上线前还应检查：

1. 对账户和来源做分层限速，并记录异常峰值。
2. 登录失败使用一致的外部提示，避免枚举账户。
3. 限制密码输入的字节长度，防止超长输入放大计算成本。
4. 日志、链路追踪和错误上报中禁止记录密码。
5. 密码重置令牌使用独立的随机值、短有效期和一次性消费机制。
6. 每次验证成功后判断参数是否过旧，并在同一认证流程中升级。

密码存储的核心不是选一个听起来最强的算法，而是建立可演进的验证格式：每条记录有随机盐，使用经过审查的慢哈希，成本经过压测，比较方式安全，并且旧参数能够在真实登录中逐步淘汰。这样即使密码库泄露，攻击者面对的也是昂贵的逐条猜测，而不是一次解密或一张表扫过全部用户。
