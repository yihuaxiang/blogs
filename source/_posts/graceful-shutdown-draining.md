---
title: 把服务停得稳：优雅关闭、连接排空与 SIGTERM 清单
date: 2026-07-09 03:04:25
tags:
  - 后端
  - 部署
  - 运维
  - 稳定性
categories:
  - 工程实践
---

![把服务停得稳](/images/graceful-shutdown-draining/cover.jpeg)

发布、扩容、缩容、节点维护都会让服务实例退出。退出本身不可怕，可怕的是进程收到 `SIGTERM` 后立刻消失：正在处理的请求被切断，队列任务做到一半，数据库事务悬着，负载均衡还在把新流量打进来。优雅关闭要解决的不是“退出得慢一点”，而是让实例从可接流状态，按顺序退到无副作用退出状态。

<!-- more -->

## 优雅关闭不是 sleep

很多故障来自一个误解：在退出脚本里 `sleep 5` 就算排空。真正可靠的关闭过程应该能回答三件事：什么时候不再接新请求，什么时候等待在途工作完成，什么时候必须强制退出。

| 阶段 | 目标 | 常见错误 |
| --- | --- | --- |
| 摘流 | 让入口停止把新请求发到旧实例 | 先关进程，再改健康检查 |
| 排空 | 给在途请求、消费者、定时任务收尾 | 无限等待，发布被卡死 |
| 清理 | 关闭数据库、缓存、日志缓冲等资源 | 直接 `process.exit(0)` |
| 兜底 | 超过预算后失败退出，交给平台重建 | 没有超时，实例半死不活 |

顺序很重要。先告诉负载均衡“我不健康了”，再等待传播和在途请求完成，最后关闭资源。如果把顺序反过来，用户看到的就是偶发连接重置、502 或重复执行。

## 先关入口，再等在途

![服务关闭生命周期](/images/graceful-shutdown-draining/shutdown-lifecycle.jpeg)

一个可维护的关闭流程通常长这样：

1. 收到 `SIGTERM` 或平台的终止通知。
2. 将 readiness 状态改为失败，停止被新流量选中。
3. 停止接受新连接，但保留已有连接。
4. 等待正在执行的 HTTP 请求、队列消息和后台任务完成。
5. 关闭数据库连接池、缓存客户端、日志缓冲。
6. 在总时间预算内正常退出；超时则带错误码退出。

下面是 Node.js 服务的简化写法。重点不在框架，而在状态机：`draining` 一旦打开，健康检查立即失败；`server.close` 停止接收新连接；最后用强制计时器兜底。

```js
let draining = false;

app.get("/readyz", (_req, res) => {
  res.status(draining ? 503 : 200).end();
});

const server = app.listen(process.env.PORT || 3000);

process.once("SIGTERM", () => shutdown("SIGTERM"));
process.once("SIGINT", () => shutdown("SIGINT"));

async function shutdown(signal) {
  if (draining) return;
  draining = true;
  console.log({ signal }, "start graceful shutdown");

  const forceExit = setTimeout(() => {
    console.error("shutdown timeout");
    process.exit(1);
  }, 25_000);
  forceExit.unref();

  server.close(async (err) => {
    try {
      if (err) throw err;
      await jobConsumer.stop();
      await dbPool.end();
      await logger.flush();
      process.exit(0);
    } catch (error) {
      console.error(error);
      process.exit(1);
    }
  });
}
```

真实项目还要单独处理 WebSocket、SSE、长轮询和上传下载。它们可能不会随着普通 HTTP 请求很快结束，需要业务层给出最大会话时长或主动通知客户端重连。

## 给排空窗口一个预算

![流量排空与实例切换](/images/graceful-shutdown-draining/draining-traffic.jpeg)

关闭窗口不能靠感觉。可以用下面的预算关系做起点：

```text
终止宽限期 >= 健康检查传播时间 + 最长请求时间 + 资源清理时间 + 安全余量
```

如果接口总超时是 8 秒，健康检查传播需要 3 秒，资源清理预计 2 秒，那么终止宽限期至少要覆盖 13 秒以上。反过来，如果平台只给 10 秒，应用就不应该允许单个请求跑 30 秒，否则每次发布都可能切断在途请求。

| 配置项 | 建议原则 |
| --- | --- |
| readiness 检查 | 进入排空后立即失败，不要等进程退出 |
| 请求超时 | 小于终止宽限期，避免关闭时无法收尾 |
| 队列消费者 | 停止拉新消息，允许当前消息 ack 或 nack |
| 资源清理 | 只做必要动作，不在退出路径里跑迁移或补偿 |
| 强制退出 | 必须存在，并记录没有收尾完成的原因 |

## 后台任务要能重放

HTTP 请求可以通过连接排空减少损失，后台任务则更依赖幂等和租约。实例退出时，消费者应该先停止拉取新消息，再处理当前消息；如果时间不够，就让消息回到队列，而不是在本地悄悄丢弃。

任务处理逻辑最好满足两个条件：重复执行不会产生重复副作用；任务持有的锁或租约有过期时间。这样即使进程在强制退出点被杀掉，后续实例也能继续处理，而不是留下永久占用的任务。

## 上线前检查表

- 收到 `SIGTERM` 后，readiness 是否马上失败。
- 负载均衡摘流后，是否还会把新请求打到旧实例。
- HTTP、队列、定时任务是否都有停止拉新和等待收尾逻辑。
- 终止宽限期是否大于健康检查传播、最长请求和清理时间之和。
- 强制退出是否会记录未完成请求、任务 ID 和关闭耗时。
- 压测或预发环境是否演练过“发布中有慢请求”的场景。

优雅关闭的价值，在平时几乎看不见，但它决定发布和缩容是否会制造随机错误。把关闭流程当成一条明确的生命周期来设计，服务就能在被替换时保持可预测：不接新活，收完旧活，清理资源，到点退出。
