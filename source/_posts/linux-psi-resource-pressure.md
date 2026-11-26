---
title: Linux PSI 资源压力：从“机器很忙”定位到谁在等待
date: 2026-11-27 03:02:06
tags:
  - Linux
  - 性能排障
  - 可观测性
categories:
  - Linux
---

![Linux 资源压力观测封面](/images/linux-psi-resource-pressure/cover.jpeg)

CPU 使用率不高，接口却变慢；内存还有余量，任务仍然排队。这类问题的共同点是：进程没有真正消耗资源，而是在等待资源。Linux Pressure Stall Information（PSI）把“有多少任务因为资源不足而停顿”变成了可读取的时间指标，适合用来补上单看 CPU、内存和 Load Average 的盲区。

<!-- more -->

## PSI 到底测什么

PSI 关注的是 stall，也就是任务本来可以运行，却因为某类资源暂时不可用而等待。它按 CPU、memory、io 分成三个维度，并提供两种视角：`some` 表示至少有一个任务在等待，`full` 表示这一时间段内所有可运行任务都在等待。对吞吐型服务来说，`some` 往往先升高；`full` 持续升高则意味着并行度几乎被耗尽。

内核通过 procfs 暴露累计窗口指标：

```bash
$ cat /proc/pressure/cpu
some avg10=0.12 avg60=0.08 avg300=0.03 total=123456789

$ cat /proc/pressure/memory
some avg10=1.45 avg60=0.91 avg300=0.40 total=9876543
full avg10=0.20 avg60=0.10 avg300=0.04 total=1234567
```

`avg10`、`avg60`、`avg300` 是最近 10、60、300 秒内的压力百分比，`total` 是累计微秒数。它们不是资源使用率，也不是请求错误率，而是“时间有多大比例被阻塞”。因此，不能把 `avg10=2` 解读为内存用了 2%。

## 三类压力分别说明什么

![PSI 文件与资源等待示意](/images/linux-psi-resource-pressure/psi-files.jpeg)

CPU PSI 上升，常见原因是运行队列过长、容器 CPU quota 太紧，或者大量线程争抢少数核。此时 `top` 里的 CPU 可能接近满载，也可能因为任务频繁睡眠而并不显眼。可以同时观察 `vmstat 1` 的运行队列、cgroup 的 `cpu.stat`，确认是主机争用还是单个工作负载被限流。

Memory PSI 反映内存回收、缺页和直接回收带来的等待。它升高时，先看匿名内存、Page Cache、swap，以及 cgroup 的 `memory.events`。如果 `full` 与 major fault 同时上升，通常说明进程已经整体停顿，延迟会比单纯的 RSS 增长更早恶化。

IO PSI 关注等待块设备或文件系统完成 I/O 的时间。写回脏页、磁盘队列堆积、网络盘抖动都可能触发它。把 IO PSI 和 `iostat -xz 1` 的 await、util，以及应用请求 RT 放在同一时间轴上，才能区分是设备慢，还是应用自身提交了过多同步 I/O。

## 从读取到告警的最小实践

先用一个短脚本每 10 秒采集一次，保留原始值和采集时间：

```bash
#!/usr/bin/env bash
set -eu
now=$(date +%s)
for kind in cpu memory io; do
  printf '%s %s ' "$now" "$kind"
  cat "/proc/pressure/$kind" | tr '\n' ' '
  echo
done
```

在容器环境中，还要确认采集范围。主机上的 `/proc/pressure` 描述整台机器，而业务决策往往需要 cgroup 视角；如果运行时提供对应的 pressure 文件，应优先采集工作负载所在层级，避免把邻居容器的压力误归因到当前服务。没有 cgroup 级数据时，也可以把节点 PSI 与容器 CPU throttling、内存事件和 Pod 重启次数并列展示，先判断压力是否来自共享宿主机。

生产监控应把 `avg10` 作为快速信号，把 `avg60` 或 `avg300` 作为持续性判断。告警条件不要只写“超过某个固定值”，还要结合服务类型建立基线。例如，批处理节点可以容忍短时 CPU `some`，在线 API 则更应关注 memory `full` 持续数分钟。告警内容同时带上节点、容器、cgroup 路径和近几分钟请求 P95，值班人员才有足够上下文。

![压力趋势与告警示意](/images/linux-psi-resource-pressure/alerting.jpeg)

## 排障时避免三个误区

第一，PSI 高不等于硬件故障。CPU 压力可能只是部署了更多副本，IO 压力也可能来自一次可预期的备份。先对齐发布、定时任务和流量变化。

第二，PSI 低不等于服务健康。锁竞争、上游超时、网络丢包不会全部表现为资源 stall，仍需结合应用指标和追踪数据。

第三，不要只看瞬时采样。`avg10` 很容易被尖峰掩盖，持续性问题应同时看 `avg60`、`avg300` 和累计 `total` 的增量。把 PSI 作为“等待证据”，再与 cgroup 限制、设备队列和请求延迟互相印证，才能从“机器很忙”走到可执行的根因判断。
