---
title: 健康检查别只返回 200：区分 Liveness、Readiness 与依赖降级
date: 2026-08-07 03:05:26
tags:
  - Kubernetes
  - 服务可靠性
  - 可观测性
categories:
  - 工程实践
---

![服务健康检查的概念插图](/images/health-check-liveness-readiness/cover.jpeg)

很多服务把 `/health` 写成“能响应就返回 200”。上线初期它看似足够，直到数据库抖动、连接池耗尽或进程进入假死：该重启的实例没有重启，该摘流量的实例仍在接请求。健康检查不是一盏绿灯，而是给编排器、负载均衡器和告警系统的操作信号。

<!-- more -->

## 先把“健康”拆成三种问题

同一个 HTTP 接口很难同时回答“进程还活着吗”“现在能接流量吗”和“启动完成了吗”。将它们分开，失败后的动作才不会互相伤害。

| 检查 | 要回答的问题 | 失败后的典型动作 | 不应包含的内容 |
| --- | --- | --- | --- |
| Liveness | 进程是否仍能推进工作 | 重启实例 | 外部数据库、缓存的瞬时失败 |
| Readiness | 实例此刻能否安全处理请求 | 从负载均衡目标中摘除 | 非关键的可选功能 |
| Startup | 初始化是否尚未完成 | 延长启动等待，避免误重启 | 稳态流量质量判断 |

Liveness 的边界应当最小：主循环是否仍在推进、关键 goroutine 是否卡死、进程是否还能调度任务。把数据库 `ping` 放进这里很危险——数据库短暂不可用时，所有实例会一起被重启，恢复时反而制造连接风暴。

Readiness 则服务于流量决策。它可以考虑“是否开始接收请求”“并发槽位是否耗尽”，以及**经过缓存和超时保护**的关键依赖状态。启动阶段很长的服务还应单独提供 Startup 检查，让初始化、预热或加载大模型时不会被 Liveness 过早杀掉。

## 让失败动作符合真实风险

一个实用的做法是将状态维护在进程内，再让探针读取快照。探针请求不应每次都同步访问所有依赖，否则它本身会放大故障。

```go
func (s *Server) Liveness(w http.ResponseWriter, r *http.Request) {
	if time.Since(s.LastProgress()) > 30*time.Second {
		http.Error(w, "stalled", http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) Readiness(w http.ResponseWriter, r *http.Request) {
	if !s.AcceptingRequests() || s.Inflight() >= s.MaxInflight() {
		http.Error(w, "draining or saturated", http.StatusServiceUnavailable)
		return
	}
	if !s.CriticalDependenciesUsable() {
		http.Error(w, "dependency unavailable", http.StatusServiceUnavailable)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
```

`LastProgress` 应由真正处理工作的路径更新，而不是由探针自己更新时间；否则卡死的业务循环仍会被探针“续命”。`CriticalDependenciesUsable` 可以由后台任务以严格超时定期刷新，探针只读取最近结果。这样既能快速摘流量，也不会在每个探针周期向数据库发起一次高峰请求。

## Readiness 是流量开关，不是重启按钮

当连接池排队、线程池满载或服务收到终止信号时，先让 Readiness 返回 503。负载均衡器停止分配新请求后，实例仍可在排空窗口内完成已有请求；窗口结束再退出。这个顺序比“直接杀进程”更能保护长请求和写操作。

![负载均衡器将流量只路由到就绪实例的插图](/images/health-check-liveness-readiness/routing-flow.jpeg)

探针阈值也必须与动作相配。Liveness 可容忍少量瞬时抖动，连续失败才重启；Readiness 可以更快失败，但恢复时设置连续成功次数，避免实例在“可用/不可用”之间来回抖动。不要把超时时间设得等于业务 P99：探针要覆盖的是一个轻量判断，而非复现一次完整请求链路。

## 给依赖划出清晰边界

判断“依赖异常是否让实例不就绪”时，先问：没有它，当前请求是否还能返回正确且可接受的结果？答案不同，处理也不同。

![应用探针与外部依赖边界的插图](/images/health-check-liveness-readiness/probe-dependencies.jpeg)

| 依赖类型 | 建议 | 示例 |
| --- | --- | --- |
| 请求必经且无降级 | 纳入 Readiness，设置短超时和缓存结果 | 主数据库、签名密钥服务 |
| 可降级功能 | 保持 Ready，在业务层返回降级结果 | 推荐、画像、非核心统计 |
| 后台异步依赖 | 用独立指标与告警观察 | 消息积压、离线索引 |

这条边界能防止“局部故障变成全站下线”。例如推荐服务不可用时，商品详情页通常仍可展示基础内容；若因此让所有实例 Not Ready，反而会把可用的主路径一并切断。

## 上线前做一次故障演练

至少验证四个场景：启动尚未完成、关键依赖断开、进程工作循环卡死、收到终止信号后的排空。观察每种场景分别触发了摘流量、重启还是告警，并记录状态变化的耗时。最后为 `live`、`ready` 的失败次数和实例被摘除数量建立监控。

健康检查的目标不是证明“一切完美”，而是让系统在不完美时采取正确动作：活着的进程不被误杀，不能服务的实例不再接流量，可降级的能力也不会拖垮主路径。
