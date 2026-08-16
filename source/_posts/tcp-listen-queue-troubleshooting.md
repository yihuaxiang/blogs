---
title: TCP 连接为何建不上：半连接队列、Accept 队列与 TIME_WAIT 排障
date: 2026-08-17 03:01:40
tags:
  - Linux
  - TCP
  - 网络诊断
  - 稳定性
categories:
  - 工程实践
---

![TCP 监听队列与连接诊断](/images/tcp-listen-queue-troubleshooting/cover.jpeg)

接口突然出现连接超时，第一反应常常是“网络抖了”。但请求到达服务器后，要先经过 TCP 握手，再进入等待应用接收的队列；任何一段跟不上，都可能表现成连不上或偶发超时。把监听端口拆成两道队列观察，比盲目调大内核参数更容易找到真正瓶颈。

<!-- more -->

## 一次连接要经过两道队列

服务调用 `listen()` 后，内核会为监听 Socket 维护两个关键阶段。收到 SYN、尚未完成三次握手的连接处于 `SYN_RECV`，可理解为半连接队列；握手完成、但应用还没调用 `accept()` 取走的连接进入 Accept 队列。

| 阶段 | 常见状态 | 堆积通常说明 |
| --- | --- | --- |
| 等待握手完成 | `SYN_RECV` | 突发建连、丢包、异常 SYN 流量 |
| 等待应用接收 | `ESTABLISHED` | 应用 accept 太慢、事件循环阻塞、CPU 饱和 |
| 已被应用处理 | `ESTABLISHED` | 进入协议读取与业务处理阶段 |

应用传给 `listen(backlog)` 的值不是无限承诺，实际 Accept 队列上限还会受到系统配置约束。半连接队列则有独立的内核管理和保护机制，因此只改一个 `backlog`，不一定能解决握手阶段的问题。

![TCP 建连经过半连接队列与 Accept 队列](/images/tcp-listen-queue-troubleshooting/connection-queues.jpeg)

## 先用现场数据判断堵点

先确认监听端口是否存在，再观察队列和 TCP 状态：

```bash
# 监听 Socket：Recv-Q 是等待 accept 的数量，Send-Q 是队列上限
ss -lntp 'sport = :443'

# 观察半连接和已建立连接
ss -nt state syn-recv 'sport = :443'
ss -nt state established 'sport = :443'

# 查看监听队列溢出、丢弃等累计计数
nstat -az | grep -E 'ListenOverflows|ListenDrops|TCPReqQFull'
```

如果监听行的 `Recv-Q` 长期贴近 `Send-Q`，同时 `ListenOverflows` 或 `ListenDrops` 增长，重点应转向应用为什么来不及 `accept()`：线程是否被同步任务占住、事件循环是否卡顿、进程是否频繁暂停，或者单实例流量是否已经超过处理能力。

若 `SYN_RECV` 突然大量增长，应结合入口负载、丢包和源地址分布判断。SYN cookies 是队列受压时的保护机制，不是日常扩容方案；只看到它启用，不能直接推断服务器遭到攻击。

## 别把 TIME_WAIT 当成故障本身

![短连接与 TIME_WAIT 的资源路径](/images/tcp-listen-queue-troubleshooting/time-wait.jpeg)

`TIME_WAIT` 通常出现在主动关闭连接的一方，用于避免旧报文干扰后续连接。数量多往往表示短连接频繁创建，不等于监听队列已经溢出。

```bash
ss -s
ss -tan state time-wait | wc -l
cat /proc/sys/net/ipv4/ip_local_port_range
```

如果服务作为客户端访问下游，连接超时且本地 `TIME_WAIT` 很高，就要检查是否没有复用 HTTP 或数据库连接，以及“目标地址、目标端口”组合是否过于集中。此时风险可能是临时端口周转压力，而不是服务端 Accept 队列。不要为了让数字变小就随意缩短回收时间；优先启用长连接、合理设置连接池，并验证超时是否真正下降。

## 按层修复，而不是一起调参

| 证据 | 优先动作 |
| --- | --- |
| Accept 队列持续满 | 排查应用停顿，提高 accept/事件处理能力，再评估 backlog |
| 半连接持续堆积 | 检查丢包、突发流量与入口防护，评估内核队列容量 |
| 短连接和 TIME_WAIT 激增 | 复用连接，限制无边界并发，检查临时端口压力 |
| TCP 建连快但首字节慢 | 转向应用线程、依赖调用和业务队列排查 |

客户端也应把“建连慢”和“响应慢”分开记录：

```bash
curl -sS -o /dev/null \
  -w 'dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} starttransfer=%{time_starttransfer}\n' \
  https://example.com/health
```

这些值都是从请求开始累计的时间，应观察相邻阶段的差值：`connect - dns` 接近 TCP 建连耗时，`tls - connect` 反映 TLS 握手，`starttransfer - tls` 则覆盖代理转发和应用处理。这样才能避免 DNS 变慢时误判为 TCP 队列故障。

## 排障清单

- 先保存 `ss -lntp`、各 TCP 状态数量和 `nstat` 计数，避免重启清空现场。
- 用计数器的增长速度判断问题，不把某个瞬时绝对值直接当结论。
- 将握手、Accept、协议处理和下游调用拆成不同延迟指标。
- 调整 `backlog` 或内核参数后，用可控压测复现并对比丢弃计数。
- 确认负载均衡后的每个实例都健康，避免总量正常但单实例热点。

TCP 连接故障的关键不是背诵参数，而是先回答连接停在哪一层：握手没完成、握手完成但应用没接走，还是连接已经进入业务处理。队列、状态和累计计数能把这三类问题分开；证据明确后再扩容、优化应用或调整内核，改动才真正可验证。
