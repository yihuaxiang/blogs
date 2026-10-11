---
title: 用 Python bisect 查区间：重复值边界、排序前提与插入成本
date: 2026-10-12 03:02:41
tags:
  - Python
  - 二分查找
  - 数据结构
categories:
  - 工程实践
---

![在有序数据中定位查询区间的两个边界](/images/python-bisect-range-boundaries/cover.jpeg)

离线分析接口延迟时，常要统计“耗时至少 20 毫秒、但不足 50 毫秒”的请求数。如果数据已经排序，每次遍历全部样本就浪费了这个前提。Python 标准库的 `bisect` 可以找到区间两端；真正容易出错的地方，是重复值落在哪一侧，以及插入数据后的成本。

<!-- more -->

## 返回的是边界，不是命中记录

`bisect_left(a, x)` 返回第一个大于等于 `x` 的位置；`bisect_right(a, x)` 返回第一个大于 `x` 的位置。若不存在这样的位置，两者都可能返回 `len(a)`。输入必须按相同的比较规则升序排列。[Python 官方文档](https://docs.python.org/3/library/bisect.html)

### 用重复值看清左右差别

对于 `[10, 20, 20, 20, 50]`，查找 `20` 的左边界为 `1`，右边界为 `4`。两个位置相减，恰好得到它出现的次数。查找不存在的 `30` 时，两者均为 `4`：这个位置适合插入 `30`，却不能证明列表中存在它。

![相同颜色的重复元素位于左右边界之间](/images/python-bisect-range-boundaries/boundaries.jpeg)

把边界直接当作下标访问，既可能取到别的值，也可能越界。判断整数值是否存在，应先检查位置，再比较元素：

```python
from bisect import bisect_left, bisect_right

samples = [10, 20, 20, 20, 50]
position = bisect_left(samples, 30)
found = position < len(samples) and samples[position] == 30
assert not found
assert bisect_right(samples, 20) - bisect_left(samples, 20) == 3
```

## 把业务区间翻译成两个位置

列表切片的结束位置不包含在结果里，因此必须先确定业务端点是否包含。设下限为 `L`、上限为 `U`，且 `L <= U`：

| 业务范围 | 起始位置 | 结束位置 |
| --- | --- | --- |
| `L <= x < U` | `bisect_left(a, L)` | `bisect_left(a, U)` |
| `L <= x <= U` | `bisect_left(a, L)` | `bisect_right(a, U)` |
| `L < x <= U` | `bisect_right(a, L)` | `bisect_right(a, U)` |

例如统计左闭右开的延迟区间，可以写成一个小函数：

```python
from bisect import bisect_left

def count_between(ordered, lower, upper):
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    start = bisect_left(ordered, lower)
    stop = bisect_left(ordered, upper)
    return stop - start

samples = sorted([50, 20, 10, 20, 80, 20])
assert count_between(samples, 20, 50) == 3
assert count_between(samples, 20, 20) == 0
assert count_between([], 20, 50) == 0
```

仅计数时，无需切片。若要返回命中的 `k` 个样本，可取 `ordered[start:stop]`，但它会新建列表并复制这 `k` 个引用。还要区分“空区间”和“端点颠倒”：前者返回零，后者应明确报错。

重复样本代表不同请求，不能先用集合去重，否则频次统计会失真。上下限相等时，左闭右开区间为空；若业务需要统计恰好等于某个阈值的请求，应使用右边界减左边界，而不是给阈值加上一个猜测的微小量。

## 排序与更新也要算进成本

### 快的是查找，插入仍可能很慢

对普通列表，且单次比较成本固定时，定位边界需要 `O(log n)` 次比较；排序预处理需要 `O(n log n)` 时间。一次查询未必值得先排序，排序后的快照被多次复用时才更有意义。

`insort` 能按顺序插入新值，但列表需要移动后面的元素，最坏成本仍是 `O(n)`。逐条插入大量乱序数据可能积累为平方级开销。离线批次可先收集再统一排序；频繁更新的大型集合则需要重新评估数据结构。

不要为了查询方便，把每次调用都写成 `bisect_left(sorted(data), x)`：这会反复排序，掩盖二分查找本身节省的工作。

如果查询还要返回原始请求，排序时必须保留延迟与请求记录的对应关系。不能只把延迟列排序，却继续用所得位置访问原始记录列表；这两个列表的同一位置已经不再代表同一个请求。

### 保住排序不变量

示例约定样本是单位一致的整数毫秒。混入字符串、特殊浮点值，或直接修改某个元素，都可能破坏比较与排序前提。共享列表若会变化，查询与更新应由同一把锁保护，或让读者使用发布后不再修改的快照。

验证时覆盖空列表、全部相等、端点之外和重复端点。对小样本，把函数结果与 `sum(lower <= x < upper for x in samples)` 比较。这个朴素实现虽慢，却直观表达业务条件，能帮助发现左右边界写反的问题。
