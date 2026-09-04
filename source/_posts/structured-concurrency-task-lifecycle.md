---
title: 异步任务别“放养”：用结构化并发收拢生命周期、取消与超时
date: 2026-09-05 03:04:17
tags:
  - Python
  - asyncio
  - 结构化并发
  - 可靠性
categories:
  - 工程实践
---

![结构化并发将异步任务约束在清晰的作用域中](/images/structured-concurrency-task-lifecycle/cover.jpeg)

一次请求同时查询库存、用户和优惠信息，看似只要创建三个异步任务就能提速。但如果请求已经失败，其中一个任务仍在后台写数据；或者主协程被取消，子任务却继续占用连接，并发就从性能工具变成了隐形故障源。结构化并发的核心不是某个库，而是一条所有权规则：任务必须属于一个明确的作用域，离开作用域前必须全部成功、失败或被取消。

<!-- more -->

## “启动后不管”为什么危险

下面的写法把审计任务交给事件循环后立即返回，调用者既不知道它何时结束，也无法可靠取得异常：

```python
async def handle(order_id):
    asyncio.create_task(write_audit(order_id))
    return await reserve_inventory(order_id)
```

服务关闭、请求超时或 `write_audit` 抛错时，这个任务没有天然的回收者。若它捕获了请求对象或数据库连接，还会延长资源寿命。真正需要脱离请求长期运行的工作，应进入有持久化、重试和监控的任务系统，而不是伪装成一条“后台协程”。

| 维度 | 放养任务 | 结构化任务组 |
| --- | --- | --- |
| 生命周期 | 可能超过调用者 | 不越过作用域边界 |
| 子任务异常 | 容易延迟暴露 | 汇总后返回调用者 |
| 调用者取消 | 子任务可能残留 | 向下传播并等待清理 |
| 资源回收 | 依赖运气 | 在退出前完成 |

![放养任务与结构化任务组的生命周期对比](/images/structured-concurrency-task-lifecycle/task-scope.jpeg)

## 用任务组建立失败边界

一个可靠的任务组应满足三件事：任一子任务失败就取消仍在运行的同伴；调用者被取消时取消全部子任务；退出前等待它们执行清理。下面的 Python 3.10 兼容封装展示了这组语义：

```python
import asyncio

async def run_group(*coroutines):
    tasks = [asyncio.create_task(c) for c in coroutines]
    try:
        done, _ = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_EXCEPTION
        )
        errors = [
            task.exception() for task in done
            if not task.cancelled() and task.exception() is not None
        ]
        if errors:
            raise errors[0]
        return [task.result() for task in tasks]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

profile, stock = await run_group(
    load_profile(user_id),
    load_stock(product_id),
)
```

生产项目可优先使用运行时或并发库提供的任务组；自制封装还要定义多异常聚合、结果顺序和重复取消等细节。无论采用哪种实现，都不要只调用 `cancel()`：取消只是发出请求，仍要等待任务结束，才能确认 `finally` 已执行、连接已归还。

## 取消必须能一路传到底

协程收到取消后，应尽快停止可中断工作，并在 `finally` 中做短小、幂等的清理。不要用宽泛的异常捕获把取消吞掉，也不要在清理阶段重新执行耗时业务。

```python
async def consume(queue, client):
    try:
        while True:
            item = await queue.get()
            await client.send(item)
            queue.task_done()
    finally:
        await client.close()
```

取消传播方向通常是从父任务到子任务；错误传播则从子任务回到父任务。两条路径闭合后，上层才能决定重试、降级或返回失败，而不是留下仍在修改状态的“幽灵任务”。

## 共享一份截止时间预算

给每个阶段各设两秒超时，会让串联流程轻易超过整体目标。入口应基于事件循环的单调时钟计算截止时间，后续步骤只消费剩余预算：

```python
loop = asyncio.get_running_loop()
deadline = loop.time() + 2.0

async def within_budget(coroutine):
    remaining = max(0.0, deadline - loop.time())
    return await asyncio.wait_for(coroutine, timeout=remaining)

profile, stock = await run_group(
    within_budget(load_profile(user_id)),
    within_budget(load_stock(product_id)),
)
await within_budget(build_offer(profile, stock))
```

![截止时间预算随并发与串行阶段逐步消耗](/images/structured-concurrency-task-lifecycle/deadline-budget.jpeg)

超时发生后，`wait_for` 会取消当前工作，而任务组负责把取消扩散到同伴并等待收尾。日志应记录总预算、实际耗时、最先失败的任务和清理耗时，避免只留下一个缺少上下文的 `TimeoutError`。

## 上线前检查生命周期

代码评审时可以沿着每个 `create_task` 追问：谁保存任务引用，谁接收异常，父任务取消时谁负责传播，退出前谁等待清理？测试至少覆盖子任务立即失败、永久阻塞、父任务取消和清理再次报错。结构化并发并不会消除失败，但它把失败限制在可观察、可回收的边界内；并发越多，这条边界越值得在设计阶段明确下来。
