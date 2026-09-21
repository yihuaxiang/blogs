---
title: Python 生成器别只当省内存：惰性流水线、一次性迭代与资源边界
date: 2026-09-22 03:02:50
tags:
  - Python
  - 生成器
  - 数据处理
categories:
  - 工程实践
---

![数据逐项流过 Python 生成器流水线的等距示意](/images/python-generator-lazy-pipelines/cover.jpeg)

一次读入百万行再处理，代码直观，却把内存峰值和首条结果的等待时间都绑在数据规模上。生成器把“先生产完、再交付”改成“需要一个、计算一个”，适合日志扫描、批量导入和分页接口。不过，惰性求值也带来一次性消费、异常延迟和资源关闭等新边界。

<!-- more -->

## 生成器改变的是控制流

包含 `yield` 的函数被调用时不会立刻执行函数体，而是返回生成器对象。每次 `next()` 让它从上次暂停处继续，产出一个值后再次暂停；函数结束时抛出 `StopIteration`，`for` 循环会自动处理这个信号。

```python
def read_events(path):
    with open(path, encoding="utf-8") as file:
        for line_no, line in enumerate(file, 1):
            line = line.strip()
            if line:
                yield line_no, line

events = read_events("events.log")  # 此时还没有打开文件
first = next(events)                 # 到这里才真正读取
```

这不只是把列表换成省内存的容器。调用方通过拉取速度决定生产速度：下游处理慢，上游自然停在 `yield`，不会无节制地把中间结果堆进内存。代价是错误也会延迟到迭代时出现，创建生成器成功不代表读取一定成功。

## 用小阶段组成惰性流水线

每个阶段只做一件事，并接收任意可迭代对象，便于复用和测试：

```python
import json

def parse_json(lines):
    for line_no, line in lines:
        try:
            yield line_no, json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: invalid JSON") from exc

def paid_orders(records):
    for line_no, record in records:
        if record.get("status") == "paid":
            yield line_no, record

pipeline = paid_orders(parse_json(read_events("events.log")))
for line_no, order in pipeline:
    save_order(order)
```

![数据读取、解析、过滤与写入按需串联的流水线](/images/python-generator-lazy-pipelines/lazy-pipeline.jpeg)

这种组合不会创建完整的解析列表和过滤列表，峰值内存主要取决于单条记录及各阶段的少量状态。异常中保留行号，能避免惰性流水线出错时只知道“迭代失败”，却找不到坏数据。

如果写入端支持批量操作，可以在末端增加固定大小的分块器，在吞吐和内存之间取得平衡：

```python
from itertools import islice

def batched(items, size):
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch

for batch in batched(pipeline, 500):
    save_orders(batch)
```

批大小不是越大越好：它同时影响单次事务时长、失败后的重做量和内存峰值，应通过真实数据测量。

## 记住它通常只能走一遍

列表是可重复迭代对象，生成器则是迭代器；它的 `iter(obj)` 返回自身，走过的元素不会自动保存。

```python
numbers = (int(text) for text in source)
maximum = max(numbers)
total = sum(numbers)  # numbers 已耗尽，这里得到 0
```

需要第二次遍历时，优先传入一个“生成器工厂”，让每次调用重新建立数据源；数据量确实很小才直接转成列表。`itertools.tee()` 会为进度不同的消费者缓存尚未被另一方读取的数据，如果一快一慢，缓存仍可能持续增长，不能把它当成免费复制。

| 需求 | 合适做法 | 主要代价 |
| --- | --- | --- |
| 单次顺序处理 | 直接传递生成器 | 消费后不可回放 |
| 少量数据反复访问 | `list(iterator)` | 一次性占用内存 |
| 可重新打开的数据源 | 使用生成器工厂 | 每轮重新读取和计算 |
| 两个近同步消费者 | 谨慎使用 `tee()` | 消费速度差会形成缓存 |

## 把资源生命周期包在迭代里

示例中的文件会在生成器正常耗尽、抛异常或被关闭时离开 `with`。问题在于调用方可能提前 `break`，又长期持有生成器引用，使文件不能及时关闭。对关键资源，应让所有权清晰可见，并显式关闭提前终止的迭代：

```python
from contextlib import closing

with closing(read_events("events.log")) as events:
    for line_no, line in events:
        if wanted(line):
            handle(line)
            break
```

![生成器提前结束时显式关闭文件资源的边界示意](/images/python-generator-lazy-pipelines/resource-boundary.jpeg)

不要让生成器返回数据库连接已经关闭后才会读取的游标，也不要在未知规模的数据上随手调用 `list()`、`sorted()` 或无界缓存；这些操作会突然打破惰性。测试时至少覆盖空输入、坏记录、处理中途异常、提前停止和完整耗尽，并断言资源已释放。生成器真正的价值，是把计算、内存与资源占用都收束到消费发生的那一刻。
