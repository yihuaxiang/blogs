---
title: 用并查集维护连通关系：路径压缩、按大小合并与使用边界
date: 2026-10-23 03:01:07
tags:
  - Python
  - 数据结构
  - 并查集
categories:
  - 工程实践
---

![独立节点组通过新连接合并为连通分量](/images/union-find-connectivity/cover.jpeg)

一批设备不断上报互通关系，后台需要反复回答：“这两台设备是否已经连在一起？”每收到一条关系就遍历整张图，容易产生重复工作。若关系只增加，可以用并查集维护分组，把查询变成寻找代表节点，把新连接变成两个集合的合并。

<!-- more -->

## 先确认问题能否归结为连通性

并查集维护互不重叠的集合。开始时，每个节点单独成组；加入无向连接后，两端所在组成为一个更大的组。它表达的是传递关系：甲连乙、乙连丙，即使甲丙没有直接连接，也属于同一连通分量。

| 业务问题 | 是否适合 | 原因 |
| --- | --- | --- |
| 新增连接后判断两点是否连通 | 适合 | 只需合并与查找 |
| 统计当前独立网络的数量 | 适合 | 成功合并时减一 |
| 查询两点之间的最短路线 | 不适合 | 集合不保存路径 |
| 删除连接后重新判断分组 | 不宜直接使用 | 普通并查集不能拆分 |

单向可达也不能直接套用。一个静态图只需遍历一次时，深度或广度优先搜索通常已经足够；并查集更适合关系逐条到达、查询交错发生的场景。

## 用父指针表示集合

### 根节点是临时代表

每个节点保存一个父节点，根节点指向自己。沿父指针找到根，就是 `find`。两个节点的根相同，表示它们同组。代表可能随合并变化，因此不要把根当作永久业务编号。

### 两种优化各做一件事

合并时，把较小树的根挂到较大树的根下，控制树高；查找时，把沿途节点直接指向根，缩短后续查找路径。前者按集合大小选择方向，后者称为路径压缩。注意：压缩改变的是父指针，原始业务连接没有因此被修改。

![父指针树在查找后变为直接指向根的浅层结构](/images/union-find-connectivity/path-compression.jpeg)

## 一个可直接运行的实现

这里使用字典支持字符串节点，所有节点必须预先登记且可哈希；未知节点会触发 `KeyError`，避免悄悄吞掉拼写错误。

```python
class UnionFind:
    def __init__(self, nodes):
        self.parent = {x: x for x in nodes}
        self.size = {x: 1 for x in self.parent}
        self.count = len(self.parent)

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while x != root:
            previous = self.parent[x]
            self.parent[x] = root
            x = previous
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        self.count -= 1
        return True


uf = UnionFind(["a", "b", "c", "d"])
assert uf.union("a", "b")
assert uf.union("b", "c")
assert not uf.union("a", "c")
assert uf.find("a") == uf.find("c")
assert uf.find("a") != uf.find("d")
assert uf.size[uf.find("c")] == 3
assert uf.count == 2
print("all checks passed")
```

重复连接和自连接都不会减少组数。`size` 只在根节点上保持有效，因此读取组大小时，要先调用 `find`，不能直接读取任意节点的旧计数。

## 复杂度与落地检查

两种优化结合后，操作序列的单次摊还成本为 `O(α(n))`；反阿克曼函数增长极慢，但这不代表每次操作都严格是常数时间。初始化与存储需要 `O(n)`。该分析按基本访问为常数成本计，Python 字典访问通常按平均常数成本估算。可对照[普林斯顿算法课程的按大小合并与路径压缩实现](https://algs4.cs.princeton.edu/15uf/WeightedQuickUnionPathCompressionUF.java.html)。

上线前，用小图的遍历结果对照连通性，覆盖孤立点、重复边、自连接和不同合并顺序。若要枚举所有分组，仍需遍历节点并按 `find` 的结果归类；并查集不会自动维护成员列表。

还要注意，`find` 本身会写父指针。多线程共享实例时，需要同步保护查找和合并的完整操作。若业务开始删除连接、追踪历史快照或解释具体路线，应重新选择数据结构，而不是继续向这个实现叠加补丁。
