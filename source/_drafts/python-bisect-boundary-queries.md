---
title: 用 Python bisect 找准边界：重复值、区间计数与档位查询
date: 2026-10-11 03:02:13
tags:
  - Python
  - 算法
  - 数据查询
categories:
  - 工程实践
---

![在有序数据中逐步缩小搜索范围，定位查询边界](/images/python-bisect-boundary-queries/cover.jpeg)

统计某个时间窗口里的事件、按阈值选择服务档位，常见写法是遍历整张列表。如果数据已经有序，Python 标准库的 `bisect` 可以直接定位边界。关键是先说清楚：查询需要包含等于端点的值吗？

<!-- more -->

## 一、返回的是插入位置

### 左边界与右边界

对升序列表，`bisect_left(a, x)` 找到第一个大于等于 `x` 的位置，`bisect_right(a, x)` 找到第一个大于 `x` 的位置。没有符合条件的元素时，两者返回列表长度；空列表则返回零。它们不会修改列表，也不会替你排序。[Python 官方文档](https://docs.python.org/3/library/bisect.html#bisect.bisect_left)

```python
from bisect import bisect_left, bisect_right

values = [10, 20, 20, 20, 40]
left = bisect_left(values, 20)
right = bisect_right(values, 20)
assert (left, right) == (1, 4)
assert right - left == 3
```

![两个边界分别位于相同值组成的连续区段两侧](/images/python-bisect-boundary-queries/boundaries.jpeg)

重复值构成连续区段，因此右边界减左边界就是出现次数。值不存在时，两者落在同一个插入位置，差为零。要访问具体元素，必须先检查索引是否小于列表长度，再确认值相等，不能把插入位置直接当作命中结果。

### 把条件翻译成位置

| 查询目标 | 位置表达式 | 访问前的检查 |
| --- | --- | --- |
| 第一个 `>= x` | `bisect_left(a, x)` | 索引小于长度 |
| 第一个 `> x` | `bisect_right(a, x)` | 索引小于长度 |
| 最后一个 `< x` | `bisect_left(a, x) - 1` | 索引大于等于零 |
| 最后一个 `<= x` | `bisect_right(a, x) - 1` | 索引大于等于零 |

特别留意减一后的负索引：Python 会把 `a[-1]` 解释为最后一个元素，无匹配值时可能悄悄返回错误结果。

## 二、统计半开时间窗口

事件时间统一为整数毫秒，列表按时间升序。窗口使用 `[start, end)`，包含开始、不包含结束；相邻窗口便不会重复计入边界事件。

```python
def count_window(times, start, end):
    if start > end:
        raise ValueError("start must not exceed end")
    lo = bisect_left(times, start)
    hi = bisect_left(times, end)
    return hi - lo

times = [1000, 2000, 2000, 3000, 4000]
assert count_window(times, 2000, 3000) == 2
assert count_window(times, 3000, 3000) == 0
assert count_window([], 1000, 2000) == 0
```

![有序时间线上仅选中左闭右开窗口中的事件](/images/python-bisect-boundary-queries/interval.jpeg)

若业务要求闭区间 `[start, end]`，结束位置改用 `bisect_right(times, end)`。只求数量时保留索引相减；取出记录才使用 `times[lo:hi]`，切片会复制结果，成本随结果数量增长。

## 三、阈值相等时归入哪一档

假设数量达到十、一百、一千时分别进入新档位，那么等于阈值应落在右侧，使用 `bisect_right`：

```python
thresholds = [10, 100, 1000]
tiers = ["basic", "small", "medium", "large"]

def tier_for(quantity):
    if quantity < 0:
        raise ValueError("quantity must be nonnegative")
    return tiers[bisect_right(thresholds, quantity)]

assert tier_for(9) == "basic"
assert tier_for(10) == "small"
assert tier_for(1000) == "large"
```

档位数组需要比阈值数组多一个元素。配置加载时检查阈值严格递增，并明确业务接受的数据类型，避免把字符串数量和整数阈值混用。

## 四、有序性与更新成本

对支持常数时间索引的普通列表，一次边界查找需要 `O(log n)` 次比较；但 `insort` 插入可能移动后续元素，总体为 `O(n)`。频繁批量更新时，可以先收集数据再排序，而不是逐条插入。[性能说明](https://docs.python.org/3/library/bisect.html#performance-notes)

查询必须使用与排序相同的比较规则。对象字段变更、降序输入、浮点数 `NaN` 都可能破坏此前提。多个线程共享列表时，应让查询与修改遵循同一把锁，或查询独立的不可变快照；仅锁住插入动作仍不足以保护查询过程。

上线前至少覆盖空列表、重复值、端点相等和超出范围四类情况，再与直接遍历的结果对照。有序列表适合反复查询边界与区间；只做精确键查找时，字典通常更直接。
