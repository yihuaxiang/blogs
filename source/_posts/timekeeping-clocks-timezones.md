---
title: 把时间处理做对：墙上时钟、单调时钟与时区边界
date: 2026-07-17 03:05:58
tags:
  - 时间处理
  - Python
  - 可靠性
categories:
  - 工程实践
---

![由时钟、服务器与地球组成的时间工程示意图](/images/timekeeping-clocks-timezones/cover.jpeg)

定时任务晚了一小时、接口明明只跑了两秒却被统计成负数、跨地区订单显示成“昨天”——这些问题往往不是计算写错了，而是程序把不同含义的“时间”混在了一起。时间处理的第一原则，不是统一格式，而是先明确用途。

<!-- more -->

## 三种问题，三种时间语义

工程中常见的时间需求可以先分成三类：

| 需求 | 应使用的基准 | 典型例子 |
| --- | --- | --- |
| 记录发生时刻 | 带时区的墙上时钟 | 创建时间、审计日志 |
| 测量经过多久 | 单调时钟 | 性能耗时、超时判断 |
| 表达当地规则 | 地区时区 | 每天 09:00 执行、营业时间 |

墙上时钟对应人类日历，但可能因人工校时或时间同步发生跳变。它适合回答“事件何时发生”，不适合用两个读数相减来计算耗时。单调时钟只保证读数不会倒退，起点没有业务含义，却正适合测量时间间隔。

```python
import time

started = time.monotonic()
run_job()
elapsed_ms = (time.monotonic() - started) * 1000
```

不要用 `time.time()` 做超时判断；一旦系统时钟向前或向后调整，等待时间就可能被意外缩短或拉长。

![墙上时钟、单调时钟与地区时区的职责分工](/images/timekeeping-clocks-timezones/clock-types.jpeg)

## 存 UTC，但不要丢掉时区意图

数据库中的事件时间适合统一保存为 UTC，接口则输出带偏移量的 ISO 8601 字符串。这样便于排序和跨系统传递，也避免服务端默认时区不同造成歧义。

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

local = datetime(2026, 7, 17, 9, 30,
                 tzinfo=ZoneInfo("Asia/Shanghai"))
stored = local.astimezone(timezone.utc)
print(stored.isoformat())  # 2026-07-17T01:30:00+00:00
```

不过，“每周一纽约时间 09:00 开会”不能只存成某个 UTC 偏移量。偏移量只是某一刻的结果，地区时区才包含夏令时规则。对于日程系统，应同时保存当地日期时间、IANA 时区名以及重复规则，再在每次生成实例时换算 UTC。

## 正视夏令时的重复与缺口

采用夏令时的地区会出现两类边界：春季拨快时，某些当地时间根本不存在；秋季拨慢时，同一个当地时间会出现两次。接收用户输入时，系统必须明确策略：拒绝不存在的时间，还是顺延到下一个有效时刻；遇到重复时间，是选择第一次还是第二次。

不要通过删除 `tzinfo` 来“解决”异常，那只是在丢失信息。Python 的 `datetime` 提供 `fold` 标记区分重复时刻，但产品层仍需把选择规则告诉用户，并在日志中记录最终采用的 UTC 时刻。

## 用截止时刻传递超时预算

多层调用若每层都配置两秒超时，总耗时可能远超两秒。更稳妥的方法是在入口用单调时钟计算截止时刻，后续步骤只使用剩余预算。

```python
import time

deadline = time.monotonic() + 2.0

def remaining():
    return max(0.0, deadline - time.monotonic())

connect(timeout=remaining())
query(timeout=remaining())
```

![请求在多个处理阶段共享同一份截止时间预算](/images/timekeeping-clocks-timezones/deadline-budget.jpeg)

剩余预算为零时应立即停止，而不是再发起注定失败的调用。日志中可以同时记录业务时间戳和单调耗时，但不要持久化单调时钟读数，因为它只在当前运行环境内有意义。

## 让时间相关测试不再等待

把“现在几点”封装成可注入的依赖，测试便能使用可推进的假时钟，而不是调用 `sleep`。重点覆盖月底、闰日、跨日、夏令时切换和系统时钟跳变等边界。一个实用检查清单是：事件时间使用带时区对象，耗时使用单调时钟，日程保留地区时区，跨层超时传递截止时刻。只要先分清语义，时间就能从隐形故障源变成可验证的工程约束。
