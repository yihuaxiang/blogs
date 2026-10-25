---
title: 用 Welford 算法计算流式方差：一次遍历、常量状态与数值稳定性
date: 2026-10-26 03:04:11
tags:
  - 算法
  - Python
  - 流式计算
  - 数值计算
categories:
  - 工程实践
---

![数据流经过统计处理器并汇聚为少量状态](/images/welford-streaming-variance/cover.jpeg)

统计一天的请求耗时，均值只能告诉我们整体水平，方差才能反映波动。但日志可能装不进内存，数据也可能持续到来。Welford 算法只保存计数、均值和离差平方和，就能边读边更新统计量，还能避开一种常见的浮点消减问题。

<!-- more -->

## 为什么直接套公式会算错

总体方差可以写成“平方的均值减去均值的平方”。数学上等价，浮点计算却可能丢失两个大数之间的小差异。下面的五个数相邻，真实总体方差是 `2`：

```python
from statistics import pvariance

xs = [float(10**12 + i) for i in range(1, 6)]
mean = sum(xs) / len(xs)
naive = sum(x * x for x in xs) / len(xs) - mean * mean
print(naive)          # 本机结果：-134217728.0
print(pvariance(xs))  # 2.0
```

方差不应为负，这里的异常来自计算过程。把负值强行改成零，只会掩盖误差。若输入能重复读取，先算均值、再累计偏差平方也是可选方案；持续到来的数据则更适合增量更新。

还要区分单位：耗时用毫秒表示，方差的单位就是毫秒的平方。若希望用原单位展示波动，应取方差的平方根得到标准差，而不是把方差直接标成“毫秒”。

## 用三个状态替代全部历史

### 更新顺序决定公式含义

设已有 `n` 条记录，均值为 `mean`，`m2` 表示各记录相对当前均值的离差平方和。新值 `x` 到来时，先记下它与旧均值之差，再更新均值，最后用新旧两个偏差更新 `m2`。

![一条新数据依次更新计数、均值和离差平方和三个状态](/images/welford-streaming-variance/running-state.jpeg)

```python
from math import isfinite

def summarize(values):
    n, mean, m2 = 0, 0.0, 0.0
    for value in values:
        x = float(value)
        if not isfinite(x):
            raise ValueError("non-finite input")
        n += 1
        delta = x - mean
        mean += delta / n
        m2 += delta * (x - mean)
    if n == 0:
        raise ValueError("empty input")
    return {
        "count": n,
        "mean": mean,
        "population_variance": m2 / n,
        "sample_variance": m2 / (n - 1) if n > 1 else None,
    }

result = summarize(iter(xs))
assert result["population_variance"] == 2.0
assert result["sample_variance"] == 2.5
```

注意最后一行更新使用的是**新均值**，不能误写成 `delta * delta`。处理 `N` 条记录需要 `O(N)` 次运算，仅保留三个统计状态；这是常量数量的状态，并不意味着 Python 整数计数器的位数永不增长。

这一步同时补偿了旧记录的中心移动：旧记录相对旧均值的偏差之和为零，因此无需逐条重新计算，就能把旧离差平方和转换到新均值下，再加入新记录的贡献。算法避免直接相减两个巨大的平方项。

### 分母必须跟统计口径一致

| 统计目标 | 分母 | 最少记录数 |
| --- | --- | --- |
| 描述当前全部记录的总体方差 | n | 1 |
| 用独立同分布样本估计总体方差 | n−1 | 2 |

单条记录的总体方差是零，样本方差却未定义，示例用 `None` 表达。空输入直接报错，避免把“没有数据”伪装成“波动为零”。两种口径可对照 [Python statistics 文档](https://docs.python.org/3/library/statistics.html#statistics.variance) 中的 `pvariance` 和 `variance`。

## 验证数值，也验证统计边界

先用常量序列检查方差为零，再用 `[1, 2, 3, 4, 5]` 检查总体方差为 `2`、样本方差为 `2.5`。将每个数加上 `10**12`，理论方差不变，可以据此暴露消减问题。对一般浮点样本，和标准库比较时应使用合理的相对及绝对容差。

异常路径至少覆盖空输入、单值、`NaN` 和无穷大。若选择跳过坏数据，应另计丢弃数量，否则统计口径会悄悄改变。输入都有限也不保证中间运算不会溢出，业务仍需约束数量级，并检查最终结果。

## 接入日志前先划清边界

统一单位后，再把解析出的耗时逐条传入函数；不要先把整个文件转成列表。按接口或时间桶分别统计时，每组都需要独立状态，内存会随分组数量增长。

假设文件每个非空行只有一个以毫秒计的数值，可以直接传入生成器；非数字内容会报错，避免静默漏掉记录：

```python
with open("latency-ms.log", encoding="utf-8") as lines:
    summary = summarize(float(line) for line in lines if line.strip())
```

Welford 改善了数值稳定性，但无法恢复输入转成浮点数时已经丢掉的信息，也不保证不同处理顺序逐位一致。累计状态也不等于滑动窗口：窗口到期需要单独管理。方差更无法直接给出 P95，定位慢请求仍需分位数或原始样本。
