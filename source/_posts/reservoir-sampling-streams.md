---
title: 数据流太大装不下：用蓄水池抽样均匀选出固定数量样本
date: 2026-09-14 03:05:12
tags:
  - 算法
  - Python
  - 流式处理
categories:
  - 工程实践
published: false
---

![持续流动的数据与容量固定的样本容器](/images/reservoir-sampling-streams/cover.jpeg)

从几十 GB 的日志里抽取一百条记录，全部读进列表再打乱会浪费内存，直接取前一百条又只反映文件开头。蓄水池抽样可以只扫描一遍，用固定数量的槽位保存样本，而且无需提前知道总记录数。它适合日志质检、数据预览和离线抽查。

<!-- more -->

## 先明确抽样承诺

设最终读到 `N` 条记录，希望抽取 `k` 条。当 `N >= k` 时，目标是从所有大小为 `k` 的记录组合中等概率选择一个；每条记录的入选概率因此都是 `k/N`。这里按出现位置抽样，相同内容出现两次，就算两条记录，并不自动去重。

| 做法 | 样本数量 | 需要注意 |
| --- | --- | --- |
| 直接取前 k 条 | 最多 k 条 | 只覆盖开头 |
| 每条独立按概率 p 保留 | 数量随机 | 无法保证固定容量 |
| 蓄水池抽样 | 最多 k 条 | 仍需遍历输入 |

下面实现经典的 Algorithm R；其替换规则可参阅 [Vitter 论文第 2 节](https://www.cs.umd.edu/~samir/498/vitter.pdf)。

## 用一次随机选择完成替换

### 入池与替换规则

先把前 `k` 条放进容器。处理第 `i` 条记录时，如果容器已满，就从 `0` 到 `i-1` 均匀选择整数 `j`：当 `j < k` 时替换第 `j` 个槽位，否则跳过当前记录。随机范围必须随已读数量增长，不能始终只在 `k` 个槽位中选，否则每条新记录都会挤掉旧记录。

![新记录经过随机选择后进入固定槽位或继续流走](/images/reservoir-sampling-streams/replacement.jpeg)

下面的 Python 代码约定 `k` 为非负整数；输入不足 `k` 条时返回全部记录，`k=0` 时直接返回且不消耗输入。

```python
import random

def reservoir_sample(items, k: int, *, rng=None):
    if k < 0:
        raise ValueError("k must be non-negative")
    if k == 0:
        return []
    if rng is None:
        rng = random.Random()

    sample = []
    for i, item in enumerate(items, start=1):
        if i <= k:
            sample.append(item)
        else:
            j = rng.randrange(i)
            if j < k:
                sample[j] = item
    return sample

result = reservoir_sample(
    iter(range(1, 11)), 3, rng=random.Random(42)
)
assert len(result) == len(set(result)) == 3
assert all(1 <= item <= 10 for item in result)
```

使用 `randrange(i)` 可以直接获得所需的整数分布，避免自行用浮点数乘法取整。独立的随机数实例也便于复现实验；需要长期复现时，同时保存种子、输入顺序和 Python 版本。[Python random 文档](https://docs.python.org/3/library/random.html#random.randrange)

### 为什么越早出现也不占优势

检查处理第 `i` 条记录后的概率：新记录能命中 `k` 个槽位之一，入选概率是 `k/i`。任意旧记录此前在池中的概率为 `k/(i-1)`；它所在槽位本轮被替换的概率为 `1/i`，所以继续留存的概率是：

```text
k/(i-1) × (1-1/i) = k/i
```

前 `k` 条最初全部入池，随后每一步都维持相同的单条入选概率；早进入只意味着经历更多次被替换的机会。

## 接到真实文件上

实际使用时，把文件迭代器直接交给函数，不要先调用 `readlines()`：

```python
with open("access.log", encoding="utf-8") as lines:
    sample = reservoir_sample(lines, 100, rng=random.Random(42))

for line in sample:
    print(line, end="")
```

算法处理 `N` 条记录需要 `O(N)` 次迭代，保存至多 `k` 条记录。这个空间上界按记录数计算：如果单条日志可能巨大，还要限制记录长度，或只保留标识及必要字段。输出的槽位顺序不代表时间顺序，也不保证随机排列。

抽样单位同样重要。若一个用户贡献了大量日志，按行均匀抽样就更容易看到这个用户；需要“每个用户机会相同”时，应先明确用户级的数据来源。若要求每个地区都有样本，则应设计分层抽样及配额，不能期待全局均匀抽样自动满足覆盖要求。

## 用小输入检查概率边界

先验证空输入、负数参数、`k=0`、输入不足以及 `k=1`。对五条互不相同的记录抽两条，后三步随机范围分别有三、四、五种结果，共有六十条等概率执行路径；枚举后，十种二元素组合都应各出现六次。这比只看一次输出“似乎随机”更能发现偏差。

长时间运行的数据流还需要明确截止点。当前函数等待输入结束才返回；无限流应按时间窗口截止，或改成可查询当前样本的状态对象。抽样省下的是保留全量数据的内存，读取成本和结果覆盖的时间范围仍需要单独管理。
