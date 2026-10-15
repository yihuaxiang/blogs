---
title: 用 Web Locks API 协调多个标签页：从互斥操作到选主
date: 2026-10-16 03:03:53
tags:
  - JavaScript
  - 浏览器
  - 并发控制
categories:
  - 前端工程
---

![多个浏览器标签页通过共享锁协作的封面](/images/web-locks-cross-tab-coordination/cover.jpeg)

同一个站点打开多个标签页后，自动保存、刷新令牌、清理本地缓存等任务可能同时运行。给每个标签页加一个 `setInterval` 并不能解决竞态：它们共享存储，却各自拥有定时器。Web Locks API 提供了一个浏览器原生的命名锁，让同源标签页围绕同一个资源排队，适合处理“同一时刻只能有一个页面执行”的本地协作问题。

<!-- more -->

## 1. 先把锁的边界想清楚

锁的名字是同源页面之间的协作协议。例如 `token-refresh` 表示刷新令牌这件事；所有标签页使用同一个名字，浏览器才会把它们放入同一条队列。默认请求的是排他锁，拿到锁的回调执行完（包括返回的 Promise 结束）后，锁才释放。

| 选项 | 行为 | 适合场景 |
| --- | --- | --- |
| 默认 | 等待前面的请求结束 | 必须串行的写操作 |
| `ifAvailable: true` | 锁被占用时立即得到 `null` | 只想“有空就做”的后台任务 |
| `signal` | 用 `AbortController` 取消排队 | 页面离开或任务超时 |
| `steal: true` | 抢占已有锁 | 极少数可丢弃旧任务的恢复场景 |

`steal` 会打断正在持有锁的回调，应当视为危险开关，而不是普通的“超时重试”。锁只存在于浏览器的协调层，不会替代服务端的数据库锁、分布式租约或权限校验。

## 2. 串行化一次本地写操作

下面的函数让多个标签页安全地更新同一个 IndexedDB 业务区。关键点是把完整的读-改-写过程放进回调，而不是只锁住最后一次写入：

```js
async function updateDraft(mutator) {
  return navigator.locks.request('draft-write', async (lock) => {
    if (!lock) return { skipped: true };

    const draft = await loadDraft();
    const next = await mutator(structuredClone(draft));
    await saveDraft(next);
    return { skipped: false, value: next };
  });
}
```

回调抛出的异常会传回 `request()` 的 Promise；异常不会把锁永久卡住，因为浏览器会在回调结束时释放它。仍然要给网络或 IndexedDB 操作设置业务超时，避免一个永不结束的 Promise 长时间占用锁。

![锁请求从排队到释放的生命周期示意](/images/web-locks-cross-tab-coordination/lock-lifecycle.jpeg)

## 3. 用短暂持有的锁选出“当前负责人”

有些任务不需要永久持锁，只要某个标签页负责一轮工作即可。`ifAvailable` 可以实现轻量的尝试选主：

```js
async function runLeaderRound() {
  await navigator.locks.request(
    'notifications-leader',
    { ifAvailable: true },
    async (lock) => {
      if (!lock) return;             // 其他标签页正在负责
      await pullNotifications();    // 只在本轮内持锁
      broadcastRefresh();
    },
  );
}

const timer = setInterval(runLeaderRound, 30_000);
window.addEventListener('pagehide', () => clearInterval(timer));
```

这不是严格意义上的永久 leader：标签页关闭、崩溃或回调完成后，下一轮可能由另一个页面接手。因此任务必须幂等，通知去重也应放在业务数据或服务端完成。若要让负责人持续存在，可以让回调等待一个可取消的 Promise，并在 `pagehide` 时调用 `AbortController.abort()`；不要依赖“某个标签页永远在线”。

![多个候选标签页竞争并选出临时负责人的示意](/images/web-locks-cross-tab-coordination/leader-election.jpeg)

## 4. 上线前的四项检查

第一，锁名要有明确的命名空间，例如 `project:tenant:resource`，避免不同模块意外互相阻塞。第二，持锁区间要短：先在锁外准备数据，再在锁内完成必要的校验与提交。第三，所有副作用都要可重试，尤其是网络请求，因为页面可能在回调中途被关闭。第四，给等待时间、跳过次数和回调耗时打点，这些指标能区分“任务没触发”和“任务一直排队”。

最后要明确适用边界：Web Locks 解决的是同源浏览器上下文之间的互斥与协调；跨设备、跨浏览器进程集群或需要审计的关键写入，仍应由服务端提供最终一致性与并发控制。把它当作前端的交通信号灯，而不是数据库里的保险箱，才能既减少重复工作，又不把可靠性押在单个标签页上。
