---
title: 任务依赖为什么排不出来：用拓扑排序检测环与安排执行
date: 2026-09-15 03:02:46
tags:
  - Python
  - 算法
  - 任务调度
categories:
  - 工程实践
published: false
---

![任务节点按依赖关系分支并汇合](/images/topological-sort-task-dependencies/cover.jpeg)

一个构建流程需要先获取数据，再生成页面与静态资源，最后打包。任务少时，手写调用顺序就够了；增加插件或调整依赖后，漏排前置任务、互相等待的问题便会出现。把任务及其依赖显式建成图，再用拓扑排序检查，可以在执行前发现矛盾，并找出当前能够开始的工作。

<!-- more -->

## 先把依赖方向说清楚

约定箭头 `A → B` 表示 B 必须等待 A 完成。任务是节点，前置关系是有向边；没有环时，这张图称为有向无环图（DAG）。拓扑序要求每条边的起点都排在终点之前，但结果通常不唯一。

| 任务 | 直接前置任务 | 开始条件 |
| --- | --- | --- |
| `fetch` | 无 | 立即可开始 |
| `assets` | `fetch` | 数据获取成功 |
| `pages` | `fetch` | 数据获取成功 |
| `bundle` | `assets`、`pages` | 两个分支都成功 |

这里 `assets` 与 `pages` 谁先执行都合法。配置只需表达必要的直接依赖，不必把所有祖先重复写进去。若两项任务会修改同一个输出文件，即使图上没有依赖，也不能据此认定它们可以安全并发。

![无环依赖图与互相等待的依赖环](/images/topological-sort-task-dependencies/dag-cycle.jpeg)

## 用标准库完成检查与执行

### 先校验，再产生副作用

Python 的 `graphlib.TopologicalSorter` 接收“任务 → 前置任务集合”的映射。注意它与箭头的阅读方向不同；只出现在前置集合里的名称，也会被自动加入图。因此，配置来自人工编辑时，需要另行拦截拼写错误。[参数说明见官方文档](https://docs.python.org/3/library/graphlib.html#graphlib.TopologicalSorter)。

下面的完整示例仅打印任务名，可保存为 `schedule.py` 后直接运行：

```python
from graphlib import CycleError, TopologicalSorter

graph = {
    "fetch": set(),
    "assets": {"fetch"},
    "pages": {"fetch"},
    "bundle": {"assets", "pages"},
}

def run(task):
    print(f"run {task}")  # 实际任务失败时应抛出异常

unknown = {dep for deps in graph.values() for dep in deps} - graph.keys()
if unknown:
    raise ValueError(f"undefined tasks: {sorted(unknown)}")

sorter = TopologicalSorter(graph)
try:
    sorter.prepare()
except CycleError as exc:
    path = " -> ".join(exc.args[1])
    raise ValueError(f"dependency cycle: {path}") from exc

while sorter.is_active():
    for task in sorted(sorter.get_ready()):
        run(task)
        sorter.done(task)
```

输出顺序为 `fetch`、`assets`、`pages`、`bundle`。`prepare()` 在首个任务执行前检查整张图；发现环时，本例直接退出。将 `graph["fetch"] = {"bundle"}` 放在构造排序器之前，就能复现循环依赖，而不会先运行部分任务。

### 理解“就绪”与“完成”

拓扑排序可以用剩余前置任务数来理解：先取出计数为零的节点；处理完一个节点，就减少它的后继节点计数；降到零的后继随后就绪。若所有节点都处理完，排序成功；若没有可处理节点却仍有剩余，图中存在环。

`get_ready()` 返回的是新就绪任务，不会自动执行它们；`done()` 表示已处理，从而解除后继的等待。上例把“处理完成”约定为成功执行，所以它必须跟在 `run()` 成功返回之后。[方法语义见官方文档](https://docs.python.org/3/library/graphlib.html#graphlib.TopologicalSorter.done)。

![下游任务等待两个前置分支全部完成](/images/topological-sort-task-dependencies/task-readiness.jpeg)

## 并发执行还需要哪些约定

这个示例是串行执行器，`sorted()` 让同一批就绪任务按名称稳定排列。改为线程池时，可以提交同批任务，但应在收到成功结果后逐个调用 `done()`；提交成功只代表进入队列。

不要在 `finally` 中无条件标记完成，否则失败任务的下游也会被放行。若失败后继续运行独立分支，还要单独记录失败、阻塞与运行中状态；不能只靠 `is_active()` 判断何时退出，否则可能一直等待永远不会完成的任务。并发数量、超时和重试也需由执行器负责。

## 用边界用例检查依赖配置

验证时，除了正常的分支汇合，还应覆盖空图、孤立任务、未定义名称、自依赖和多节点环。检查结果时，重点断言“每个前置任务都先于依赖它的任务完成”，避免把某个合法顺序误当成唯一答案。

出现环时，应修正依赖关系，或把相互依赖的步骤拆成准备、执行等阶段。删掉一条真实依赖只会让排序通过，无法保证执行正确。依赖图准确描述任务契约，排序器才能成为可靠的执行入口。
