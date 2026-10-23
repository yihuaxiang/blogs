---
title: 用 Dijkstra 求最短路径：非负权重、优先队列与过期状态
date: 2026-10-24 03:02:33
tags:
  - Python
  - 图算法
  - Dijkstra
categories:
  - 工程实践
published: false
---

![网络中的低成本路线与其他候选路线](/images/dijkstra-shortest-paths/cover.jpeg)

配送线路、服务路由都可能需要比较多条路线的总成本。跳数少未必成本低：一条直达线路可能比两次中转更贵。对于边权非负的图，Dijkstra 能从一个起点求出到各个可达节点的最短距离。

<!-- more -->

## 先确认权重满足前提

把位置作为节点，把单向连接作为边，耗时或费用作为权重；双向连接要登记两条边。算法假设计算期间图不变，路径成本可以逐边相加。

| 图的条件 | 可选方法 |
| --- | --- |
| 所有边成本相同 | 广度优先搜索 |
| 权重非负，允许零权边 | Dijkstra |
| 存在负权边 | 考虑 Bellman-Ford，并检查负环 |

不要通过给每条边加常数来消除负权：不同路线包含的边数不同，加完后的最优路线可能改变。

## 每次扩展当前最近的节点

### 松弛就是尝试更短的走法

设起点为 A，边为 A→B（8）、A→C（2）、C→B（1）。直接到 B 的候选距离是 8，经 C 可以降到 3。用“到当前节点的距离＋边权”更新邻居，这一步称为松弛。

每次从候选中取距离最小的节点。因为后续边不会降低成本，这个有效候选的距离可以确定。负权边会破坏这一依据。原理可对照[普林斯顿算法课程的最短路径说明](https://algs4.cs.princeton.edu/44sp/)。

### 发现节点时不能立刻封口

B 第一次入队时距离为 8，此时还不能把它标为最终结果。使用最小堆后，C 会先出队，随后把 B 的更优候选放入堆中。

![已确定节点、搜索前沿与被丢弃的过期候选](/images/dijkstra-shortest-paths/frontier.jpeg)

## 用过期检查简化优先队列

Python 的 `heapq` 提供[最小堆操作](https://docs.python.org/3/library/heapq.html)。这里不修改堆内旧条目，而是插入新候选；旧条目出队时，如果距离已经落后，就直接跳过。

示例约定节点名为字符串，权重为非负整数；字符串让等距离候选仍能比较。返回字典只包含可达节点。

```python
import heapq


def dijkstra(graph, start):
    if any(w < 0 for edges in graph.values() for _, w in edges):
        raise ValueError("negative edge")
    dist = {start: 0}
    heap = [(0, start)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost != dist[node]:
            continue
        for neighbor, weight in graph.get(node, ()):
            candidate = cost + weight
            if candidate < dist.get(neighbor, float("inf")):
                dist[neighbor] = candidate
                heapq.heappush(heap, (candidate, neighbor))
    return dist


graph = {"A": [("B", 8), ("C", 2)], "C": [("B", 1)]}
assert dijkstra(graph, "A") == {"A": 0, "B": 3, "C": 2}
```

代码会先扫描整张邻接表，因此即使负边位于不可达区域也会拒绝输入。缺少出边的节点可以省略键；起点即使没有出边，仍会以距离零出现在结果中。对外接口还应校验节点类型、权重类型及单位。

严格使用 `<` 更新，避免零权环反复产生相同距离的候选。若节点换成不可排序的对象，可把条目改成 `(距离, 递增序号, 节点)`，避免堆在距离相同时比较节点。

## 把返回结果与工程边界说清楚

这里求的是距离。若要还原路线，在距离改善时记录前驱，再从终点反向回溯。只查一个终点时，可以在它通过过期检查、正式出队后提前返回，不能在首次入队时返回。

设 V、E 为节点数和边数，此实现包含全图负权检查，时间上界为 `O(V + E log(E+1))`；堆可能保留旧候选，辅助空间为 `O(V+E)`。不要把它误记成只存 V 个候选的实现。

落地前覆盖孤立节点、零权环、平行边和先发现较贵路线的情况，再用小图与朴素算法对照。若权重实际依赖出发时间，或资源约束无法简单相加，需要重新建模，不能直接把动态成本塞进这个静态图实现。
