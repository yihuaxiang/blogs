---
title: 把缓存用稳：Cache-Aside、TTL 与一致性取舍清单
date: 2026-07-01 03:03:41
tags:
  - 缓存
  - 后端
  - 性能
  - 可靠性
categories:
  - 工程实践
---

![把缓存用稳](/images/cache-aside-consistency-patterns/cover.jpeg)

缓存不是“给慢接口加一层 Redis”这么简单。它能降低数据库压力、缩短高频读取的响应时间，也会引入过期数据、穿透、击穿、雪崩和回源放大。要把缓存用稳，关键是先承认它只是数据副本，再围绕读写路径、过期策略和故障边界做工程约束。

<!-- more -->

## 先确认缓存解决什么问题

不是所有慢接口都应该先加缓存。如果慢来自 N+1 查询、缺少索引或下游没有超时，缓存只会把问题藏起来。更适合缓存的是“读取频繁、短时间可接受旧值、计算或查询成本高”的数据，例如商品详情、权限快照、配置项、榜单片段和聚合统计。

| 问题 | 缓存能帮什么 | 仍要补上的约束 |
| --- | --- | --- |
| 热点读压垮数据库 | 把大部分读请求挡在缓存层 | 监控命中率和回源量 |
| 计算结果昂贵 | 复用最近一次结果 | 明确 TTL 和失效方式 |
| 短暂下游抖动 | 返回仍可接受的旧数据 | 标记数据版本和更新时间 |

判断标准可以很直接：如果业务无法接受几秒到几分钟的旧值，就不要把缓存当主路径；如果缓存失效会让数据库瞬间扛不住，就必须先设计保护措施。

## Cache-Aside 的基本形状

![缓存读取链路](/images/cache-aside-consistency-patterns/read-path.jpeg)

Cache-Aside 是最常见的应用侧缓存模式：读时先查缓存，未命中再查数据库，并把结果写回缓存；写时先改数据库，再删除相关缓存。它的优点是简单、可局部引入，缺点是应用要自己处理并发、过期和补偿。

```js
async function getProduct(id) {
  const key = `product:${id}:v1`;
  const cached = await cache.get(key);
  if (cached) return JSON.parse(cached);

  const product = await db.products.findById(id);
  if (!product) {
    await cache.set(key, JSON.stringify(null), { ttl: 30 });
    return null;
  }

  await cache.set(key, JSON.stringify(product), {
    ttl: ttlWithJitter(300, 60)
  });
  return product;
}

function ttlWithJitter(baseSeconds, jitterSeconds) {
  return baseSeconds + Math.floor(Math.random() * jitterSeconds);
}
```

这里有两个细节。第一，空结果也短暂缓存，避免不存在的 ID 持续打到数据库。第二，TTL 加随机抖动，避免大量 key 在同一秒集体过期。写路径通常不要“更新数据库后再更新缓存”，因为并发写很容易把旧值覆盖回去；更稳妥的是更新数据库成功后删除缓存，让下一次读取重新加载。

## Key、TTL 和失效范围要可控

缓存 key 要把影响结果的维度写清楚，例如租户、语言、权限版本、接口版本和查询参数。不要把整个 URL 或 JSON 随手拼进去，也不要让同一份业务数据散落成许多没有关系的 key。命名可以从粗到细：

```txt
tenant:{tenantId}:product:{productId}:detail:v2
tenant:{tenantId}:user:{userId}:permissions:v5
```

TTL 不是越长越省事。配置、权限、库存这类数据要有明确的失效入口；榜单、推荐、统计可以接受固定周期刷新；热点详情页可以用较长 TTL 搭配主动删除。对强一致要求高的字段，宁愿少缓存或只缓存不参与决策的展示数据。

## 防穿透、击穿和雪崩

![缓存失效治理](/images/cache-aside-consistency-patterns/failure-control.jpeg)

穿透是请求绕过缓存持续打到数据库，常见于不存在的 ID 或恶意枚举。可以用参数校验、布隆过滤器、空值短缓存降低风险。击穿是单个热点 key 过期后，大量请求同时回源；可以用 single-flight 锁，让同一时刻只有一个请求加载数据库，其余请求等待或返回旧值。雪崩是大量 key 同时失效，随机 TTL、分批预热和限流能减少尖峰。

```js
async function loadOnce(key, loader) {
  const lockKey = `lock:${key}`;
  if (await cache.set(lockKey, "1", { nx: true, ttl: 5 })) {
    try {
      const value = await loader();
      await cache.set(key, JSON.stringify(value), { ttl: ttlWithJitter(300, 60) });
      return value;
    } finally {
      await cache.del(lockKey);
    }
  }

  await sleep(50);
  const cached = await cache.get(key);
  return cached ? JSON.parse(cached) : loader();
}
```

锁的 TTL 要短，释放失败也不能长期阻塞；等待方要有超时，不能在缓存层制造新的排队事故。

## 监控比口号更重要

缓存上线后至少看四类指标：命中率、回源 QPS、缓存错误率、加载耗时。命中率下降不一定是坏事，可能是业务结构变了；回源 QPS 突增才更接近风险信号。日志里也要记录 `cache_key_prefix`、`hit`、`ttl_seconds` 和 `load_duration_ms`，但不要打印完整敏感 key。

最后给缓存设计留一条底线：缓存可以提升性能，但不能成为唯一真相。数据库仍然是权威数据源，缓存策略要能解释“旧值最多保留多久、失效失败如何补偿、缓存不可用时系统如何降级”。把这些问题写清楚，比事后补一个更大的 Redis 集群更可靠。
