---
title: Linux 内存告警别只看 free：RSS、PSS、Page Cache 与 OOM 排查
date: 2026-07-21 03:04:44
tags:
  - Linux
  - 内存管理
  - 故障排查
categories:
  - 运维与调试
---

![Linux 内存排障封面](/images/linux-memory-troubleshooting/cover.jpeg)

服务器提示“内存快满了”，第一反应往往是找出 RSS 最大的进程，甚至直接重启服务。但 Linux 会主动利用空闲内存缓存文件，同一份共享页也可能被重复计入多个进程。先分清指标含义，再沿着系统、cgroup、进程三层收集证据，才能判断是正常缓存、真实泄漏，还是容器限额过小。

<!-- more -->

## 先建立正确的内存账本

`free -h` 中的 `free` 很低并不等于内存紧张。Linux 会把暂时不用的内存用于 Page Cache；应用需要时，这部分通常可以回收。判断主机余量应优先看 `available`，再结合换页与内存压力观察趋势。

进程指标也不能只看一个数字：

| 指标 | 含义 | 排障价值 |
| --- | --- | --- |
| VSZ / VIRT | 已映射的虚拟地址空间 | 可能只预留地址，不能代表实际占用 |
| RSS | 当前驻留在物理内存中的页 | 便于快速排序，但共享页会在各进程重复计算 |
| PSS | 按共享者数量平摊后的驻留页 | 更适合估算一组进程的真实成本 |
| Swap | 已换出的匿名页 | 持续增长常意味着工作集超过可用内存 |

因此，“所有进程 RSS 相加”通常不会等于主机已用内存。文件缓存、内核内存、共享库和容器统计口径都会造成差异。

## 三层定位：主机、容器、进程

### 1. 主机层：确认是不是真有压力

先保存现场，而不是急着清缓存：

```bash
free -h
vmstat 1 10
cat /proc/pressure/memory
journalctl -k -g 'oom|Out of memory|Killed process'
```

`vmstat` 的 `si`、`so` 表示换入换出；偶发非零不一定有问题，持续换页并伴随延迟升高才值得警惕。PSI 中 `some` 表示至少有任务因内存等待，`full` 表示所有非空闲任务都被阻塞，比单次“使用率”更接近用户感受到的压力。

### 2. cgroup 层：检查是否撞到局部上限

容器可能在主机尚有余量时被 OOM Kill。cgroup v2 可查看：

```bash
CG=/sys/fs/cgroup/my-service
cat "$CG/memory.current"
cat "$CG/memory.max"
cat "$CG/memory.events"
```

`memory.current` 是当前用量，`memory.max` 是硬上限；`memory.events` 中 `oom` 或 `oom_kill` 增长，说明该 cgroup 确实触发过内存不足。排查时记录计数变化，不要仅凭进程退出码猜测。

![从主机到进程逐层分析内存](/images/linux-memory-troubleshooting/diagnostic-layers.jpeg)

### 3. 进程层：拆开匿名页与文件页

先按 RSS 找候选，再读取更准确的汇总：

```bash
ps -eo pid,ppid,rss,vsz,comm --sort=-rss | head

PID=1234
cat "/proc/$PID/smaps_rollup"
cat "/proc/$PID/status" | grep -E 'VmRSS|VmSwap|Threads'
```

`smaps_rollup` 中，`Pss` 适合比较多进程服务，`Anonymous` 更接近堆、栈等匿名内存，`Swap` 能确认该进程是否被换出。若进程使用大量 `mmap` 文件，RSS 上升可能只是文件页进入内存，并非堆泄漏。

## 用时间序列证明“泄漏”

泄漏是一种持续增长且无法回落的行为，不是一张截图。固定负载下每隔一分钟记录 `Pss`、`Anonymous`、请求量与 GC 指标，跨过多个业务周期再比较：

```bash
while kill -0 "$PID" 2>/dev/null; do
  date '+%F %T'
  grep -E '^(Pss|Anonymous|Swap):' "/proc/$PID/smaps_rollup"
  sleep 60
done
```

如果 PSS 随请求累计单调上升，低峰和 GC 后也不回落，再进入语言运行时的堆快照或分配分析；如果主要增长来自文件页且系统没有 PSI 压力，通常应检查缓存策略，而不是贸然认定泄漏。

## 常见误区与处理顺序

不要把 `echo 3 > /proc/sys/vm/drop_caches` 当作修复，它会丢弃可复用缓存并制造额外 I/O；也不要只提高容器上限，这可能只是推迟下一次 OOM。更稳妥的顺序是：保留 OOM 日志和指标，确认限制边界，识别增长类型，复现趋势，最后才调整缓存容量、并发、运行时参数或代码。

一套可靠的内存排障结论应能回答三个问题：压力发生在哪个边界，增长的是哪类页面，什么负载让它持续增长。把这三点说清楚，重启就不再是唯一答案。
