---
title: Linux cgroups v2 资源治理：CPU、内存、IO 限制与 OOM 排障
date: 2026-09-06 03:03:43
tags:
  - Linux
  - 容器
  - 性能排障
categories:
  - 工程实践
---

![cgroups v2 资源治理封面](/images/linux-cgroups-v2-resource-governance/cover.jpeg)

当一台机器同时运行 Web 服务、批处理和构建任务时，“放进容器”不等于资源已经被治理。没有配额时，构建进程可能吃光内存，邻近服务被 OOM killer 终止；CPU 抢占和磁盘 IO 争用也会让延迟抖动。cgroups v2 把上限、权重和观测数据绑定到同一个工作负载上。

<!-- more -->

## 先理解层级：目录就是边界

cgroups v2 通常挂载在 `/sys/fs/cgroup`，每个子目录代表一个控制组。进程写入 `cgroup.procs` 后继承该组限制。v2 使用统一层级，迁移和排障更容易复现。

```bash
# 查看当前层级和控制器
mount | grep cgroup2
cat /sys/fs/cgroup/cgroup.controllers

# 创建构建组，并启用控制器
sudo mkdir /sys/fs/cgroup/build
echo "+cpu +memory +io" | sudo tee /sys/fs/cgroup/cgroup.subtree_control
```

启用控制器要在父组完成，子组才能看到 `cpu.*`、`memory.*` 和 `io.*` 文件。生产环境通常由 systemd 或容器运行时管理，手工命令适合实验和定位。

## CPU：限制峰值，也要保留公平性

`cpu.max` 用“配额 周期”描述上限。`50000 100000` 表示每 100 毫秒最多运行 50 毫秒，约半个 CPU。`cpu.weight` 则决定竞争时的相对份额。

```bash
echo "50000 100000" | sudo tee /sys/fs/cgroup/build/cpu.max
# weight 越大，竞争时获得的 CPU 时间越多
echo 200 | sudo tee /sys/fs/cgroup/build/cpu.weight
```

排查限速先看 `cpu.stat` 的 `nr_throttled` 和 `throttled_usec`。两者持续增长且延迟上升，说明配额过紧；若没有限速但 load 很高，应继续查运行队列、IO 等待或锁竞争。

![CPU 配额与调度权重示意图](/images/linux-cgroups-v2-resource-governance/cpu-quota.jpeg)

## 内存：软边界、硬上限和事件

`memory.high` 是压力线，超过后内核会回收或让分配变慢；`memory.max` 是硬上限，无法回收时可能在该 cgroup 内触发 OOM。先观察 `high` 事件，再把 `max` 设为最后防线。

```bash
echo 2G | sudo tee /sys/fs/cgroup/build/memory.high
echo 3G | sudo tee /sys/fs/cgroup/build/memory.max
cat /sys/fs/cgroup/build/memory.current
cat /sys/fs/cgroup/build/memory.events
```

`memory.events` 的 `high`、`max`、`oom`、`oom_kill` 能区分压力和杀进程。发生 OOM 后同时记录 `memory.current`、`memory.stat` 与进程参数；RSS、page cache 和内核内存都可能计入 cgroup。

![内存压力线与 OOM 事件示意图](/images/linux-cgroups-v2-resource-governance/memory-pressure.jpeg)

## IO：先确认设备号

`io.weight` 只改变竞争中的相对份额；`io.max` 才能限制带宽或 IOPS。先用 `lsblk` 或 `stat -c '%t:%T' 文件` 确认设备号，写错设备号时配置不会真正生效。

```bash
# 限制 8:0 设备的写带宽
echo "8:0 wbps=50M" | sudo tee /sys/fs/cgroup/build/io.max
echo "8:0 100" | sudo tee /sys/fs/cgroup/build/io.weight
cat /sys/fs/cgroup/build/io.stat
```

IO 延迟升高时，把 `io.stat`、磁盘监控和请求耗时放在同一时间线，才能区分限额、设备饱和与应用写放大。

## 一套可回滚的落地顺序

| 步骤 | 动作 | 观察指标 |
| --- | --- | --- |
| 1 | 只创建 cgroup | 记录三类指标基线 |
| 2 | 设置 weight 与 `memory.high` | 看延迟和回收事件 |
| 3 | 再设各类 max | 观察限速与 OOM |
| 4 | 压测超限场景 | 验证告警和回滚 |

所有限制都应纳入配置管理，并保留删除组或写回 `max` 的回滚命令。上线后告警覆盖 `oom_kill`、持续增长的 `nr_throttled` 和 IO 延迟分位数，cgroups 才是一套可观测、可撤销的资源策略。
