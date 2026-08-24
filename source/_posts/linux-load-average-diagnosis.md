---
title: Linux 负载高不等于 CPU 忙：读懂 Load Average、运行队列与 iowait
date: 2026-08-25 03:04:35
tags:
  - Linux
  - 性能诊断
  - 故障排查
categories:
  - 运维与调试
---

![Linux 系统负载诊断封面](/images/linux-load-average-diagnosis/cover.jpeg)

告警显示 Load Average 突然升到 20，服务器就一定是 CPU 不够吗？不一定。Linux 的系统负载既包含正在使用或等待 CPU 的任务，也包含处于不可中断睡眠的任务。后者常在等待磁盘、网络文件系统或内核 I/O。把负载直接当成 CPU 使用率，扩容很可能扩错方向。

<!-- more -->

## Load Average 到底数了什么

`uptime` 展示的三个数字，是最近 1、5、15 分钟负载的指数移动平均值，并非三个互不相干的时间桶。Linux 主要把两类任务计入负载：可运行状态 `R`，以及不可中断睡眠状态 `D`。因此，CPU 运行队列变长会推高负载，大量任务卡在 I/O 上也会推高负载。

先把数字放回机器容量中观察：8 核主机负载为 8 和单核主机负载为 8，含义完全不同。可用下面的命令建立基线：

```bash
uptime
nproc
cat /proc/loadavg
```

`/proc/loadavg` 的前三列仍是 1、5、15 分钟负载；第四列形如 `2/813`，表示当前可运行任务数与任务总数；最后一列是最近创建任务的 PID。粗略比较时可计算 `load1 / CPU 数`，但容器还要使用 CPU quota 对应的有效核数，不能照搬宿主机核数。

![运行队列与 CPU 核心的关系](/images/linux-load-average-diagnosis/run-queue.jpeg)

## 第一轮采样：先分 CPU 与等待

不要看到告警就重启。先连续采样，而不是只保存一个瞬时值：

```bash
date
uptime
nproc
vmstat 1 10
cat /proc/pressure/cpu
cat /proc/pressure/io
```

`vmstat` 是最实用的分岔口。`r` 是可运行任务数，持续明显高于有效 CPU 数且 `id` 接近零，通常指向 CPU 饱和；`b` 是不可中断等待任务数，持续升高则应检查 I/O。`wa` 表示 CPU 空闲且系统存在未完成 I/O 的时间，并不等于磁盘繁忙百分比；虚拟机中的 `st` 偏高，则说明 CPU 时间被宿主机拿走。PSI 还能回答任务是否真的因 CPU 或 I/O 争用而停顿。

| 现场组合 | 更可能的方向 | 下一步证据 |
| --- | --- | --- |
| `r` 高、`id` 低、CPU pressure 高 | CPU 竞争 | 每核利用率、热点进程、上下文切换 |
| `b` 高、D 状态多、I/O pressure 高 | I/O 或内核等待 | 设备延迟、进程 I/O、等待点 |
| `st` 高 | 虚拟化层争用 | 同期宿主机或云平台指标 |
| 总负载不高但单核满 | 单线程瓶颈或绑核 | 每核利用率、线程级采样 |

## 沿证据链找到责任对象

### CPU 路径

确认 CPU 竞争后，再定位不均衡和热点：

```bash
mpstat -P ALL 1
pidstat -u -w 1
ps -eo pid,ppid,stat,psr,pcpu,comm --sort=-pcpu | head
```

`mpstat` 能发现某个核心打满而其他核心空闲；`pidstat` 同时观察进程 CPU 与上下文切换。单线程程序在 32 核机器上可能只贡献约 1 的负载，却已经把关键请求串行化。确定热点进程后，再用语言 profiler 或火焰图找具体函数，而不是先猜内核参数。

### I/O 与阻塞路径

如果 `b`、I/O PSI 或 D 状态上升，应继续确认谁在等待、等待什么：

```bash
iostat -xz 1
pidstat -d 1
ps -eo state,pid,wchan:32,comm | awk '$1 ~ /^D/'
```

把设备延迟、队列变化与进程读写速率对齐。`wchan` 可提供内核等待点线索，但不要凭一次采样下结论：本地磁盘、网络盘、块设备故障和换页都可能产生类似现象。若 D 状态任务集中在同一挂载点，应同步检查内核日志、挂载端与存储端指标。

![从 CPU、存储和阻塞任务分流排查](/images/linux-load-average-diagnosis/bottleneck-paths.jpeg)

## 告警与处置应绑定用户影响

固定写成“负载超过 10”无法适配不同机器。更稳妥的告警条件是：归一化负载持续超出基线，同时请求延迟、CPU/IO PSI 或队列长度恶化。CPU 路径可通过限制并发、消除热点或扩容处理；I/O 路径应治理慢设备、异常挂载、写入洪峰或无界队列；`st` 高则要处理虚拟化资源争用。

Load Average 是烟雾报警器，不是火源定位器。先用趋势判断压力是否持续，再用 `vmstat` 选择分支，最后以进程、设备和等待点完成闭环，才能把“负载高”变成可验证、可复现的故障结论。
