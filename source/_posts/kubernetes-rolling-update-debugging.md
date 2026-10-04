---
title: Kubernetes Deployment 滚动更新排障：副本预算、修订与可逆回滚
date: 2026-10-05 03:05:47
tags:
  - Kubernetes
  - 容器编排
  - 发布工程
categories:
  - 工程实践
---

![Kubernetes Deployment 滚动更新封面](/images/kubernetes-rolling-update-debugging/cover.jpeg)

Deployment 更新卡住时，盯着 Pod 日志往往只能看到结果，找不到控制器为什么不再推进。滚动更新其实是一组可计算的约束：新旧 ReplicaSet 如何交替扩缩、最多允许多少副本不可用，以及哪些 Pod 仍属于这个 Deployment。把这些约束拆开，排障就能从“反复重启”变成可验证的检查。

<!-- more -->

## 先画出控制器的对象链

Deployment 不直接管理容器，而是管理 ReplicaSet；ReplicaSet 再负责维持带有特定 Pod template hash 的 Pod。修改镜像、环境变量或模板标签后，Deployment 创建新的 ReplicaSet，旧 ReplicaSet 暂时保留。滚动更新的过程，就是在副本总量和可用性约束内，把期望数量逐步交给新 ReplicaSet。

![控制器协调新旧 ReplicaSet 的流程示意](/images/kubernetes-rolling-update-debugging/deployment-flow.jpeg)

因此，第一步不要只执行 `kubectl get pods`，而要同时查看三层对象：

```bash
kubectl get deploy,rs,pods -l app=checkout -o wide
kubectl describe deployment checkout
kubectl rollout status deployment/checkout --timeout=90s
```

`describe deployment` 的 Events 通常能说明是副本不足、调度失败还是更新进度超时；ReplicaSet 的 `DESIRED/CURRENT/READY` 则能告诉你卡在扩容、收缩还是 Pod 尚未达到可用条件。

## 用副本预算决定更新速度

`strategy.rollingUpdate` 的两个字段决定“能多快换完”和“最多损失多少容量”。`maxSurge` 允许更新期间临时多出的副本，`maxUnavailable` 允许低于期望数量的副本。两者都设得过于保守，发布会像停住；都放得太大，又可能让节点资源或下游连接瞬间吃紧。

| 配置 | 作用 | 排障时要问的问题 |
| --- | --- | --- |
| `replicas` | 稳态期望副本数 | 当前是否被 HPA 或别的工具改写 |
| `maxSurge` | 更新期间可额外创建的数量 | 节点是否有余量调度新 Pod |
| `maxUnavailable` | 更新期间允许少掉的数量 | 旧 Pod 是否因为预算无法继续删除 |
| `minReadySeconds` | Pod 连续可用多久才计入进度 | 新版本是否刚可用就再次失败 |

例如六副本服务可以先采用“一增一减”的节奏，给连接和缓存预热留下空间：

```yaml
spec:
  replicas: 6
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 1
  minReadySeconds: 10
  progressDeadlineSeconds: 600
```

`progressDeadlineSeconds` 只会把“长时间没有进展”记录为失败条件，不会替你自动回滚。生产发布脚本应明确检测 `rollout status` 的退出结果，再决定暂停、回滚或人工介入。

## 四类常见的“卡住”

**新 ReplicaSet 没有 Pod。** 先看 Events 和节点资源，再看 Pod 的调度约束、污点容忍和镜像拉取。若新模板使用了不存在的 ServiceAccount 或 Secret，Pod 可能一直无法创建或启动。

**新旧副本都在，但旧副本降不下来。** 检查 `maxUnavailable`、PodDisruptionBudget 以及是否有 Pod 长时间处于终止状态。PDB 约束的是自愿中断，和 Deployment 的更新预算叠加后，可能让收缩速度比预期更慢。

**Pod 数量来回变化。** 确认是否同时存在 HPA、手工 `kubectl scale` 或 GitOps 控制器。多个写入者争夺 `spec.replicas` 时，Deployment 本身没有办法判断谁的目标才是正确的。

**更新看似完成却接入了旧 Pod。** 核对 Service selector 与 Deployment selector 是否指向同一组标签。Deployment 的 selector 创建后不可随意修改；模板标签若与其他工作负载重叠，Service 可能把不属于本次发布的 Pod 也选进来。

![Deployment 修订、事件和副本预算的排障视图](/images/kubernetes-rolling-update-debugging/rollout-debug.jpeg)

## 把回滚做成可验证动作

发布前保留合理的 `revisionHistoryLimit`，并记录镜像摘要、配置变更和发布人。出现错误时先冻结继续变化的来源，再查看历史：

```bash
kubectl rollout pause deployment/checkout
kubectl rollout history deployment/checkout
kubectl rollout undo deployment/checkout --to-revision=4
kubectl rollout resume deployment/checkout
kubectl rollout status deployment/checkout --timeout=120s
```

回滚后仍要检查 Service 端点、错误率和新旧 ReplicaSet 数量。不要把删除旧 ReplicaSet 当作清理动作：它们承载着可回退的模板，过早清理会让下一次故障失去安全垫。

## 终止旧 Pod 也属于发布设计

旧 Pod 收到终止信号后，需要在 `terminationGracePeriodSeconds` 内完成连接排空和状态落盘。应用应处理 SIGTERM，停止接收新任务，再等待正在执行的请求结束；仅仅把宽限期调大，不能修复没有排空逻辑的进程。若发布期间旧 Pod 长时间 Terminating，应检查 finalizer、挂载卸载和 preStop 钩子，而不是直接反复删除 Pod。

一次可靠的滚动更新应当能回答三件事：当前由哪个 ReplicaSet 提供容量，预算为何允许或阻止下一步动作，以及失败后能否回到上一个已验证修订。把这三点写进发布脚本和运行手册，Deployment 的“卡住”就会变成一组可以复现、观测和回滚的状态转换。
