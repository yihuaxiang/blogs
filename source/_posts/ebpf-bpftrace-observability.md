---
title: 用 eBPF 与 bpftrace 观察 Linux 运行时：低侵入排障入门
date: 2026-11-05 03:06:34
tags:
  - Linux
  - eBPF
  - bpftrace
  - 可观测性
categories:
  - 工程实践
---

![Linux 内核事件经过 eBPF 探针流向观测面板](/images/ebpf-bpftrace-observability/cover.jpeg)

线上服务出现延迟尖峰时，重启只能让证据消失，加日志又可能改变时序。eBPF 允许在内核和用户态关键位置挂载程序，把系统调用、调度、网络和文件事件汇总出来；bpftrace 用接近脚本的语法，把排障变成可复现的观测命令。本文给出不改业务代码定位路径。

<!-- more -->

## eBPF 解决什么问题

日志不一定知道线程何时被调度；抓包也解释不了进程为何迟迟没有 `read`。eBPF 探针可以挂在 tracepoint、kprobe 或用户态函数，记录时间、进程和参数，再通过 ring buffer 或 map 交给用户态。

![eBPF 探针从内核钩子进入用户态缓冲区](/images/ebpf-bpftrace-observability/probe-lifecycle.jpeg)

## 用 bpftrace 做三步定位

### 1. 先确认进程在做什么

遇到启动慢，先看是否反复拉起子进程，或频繁打开不存在的配置：

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_openat
/comm == "my-service"/
{ printf("%s pid=%d file=%s\n", comm, pid, str(args->filename)); }'
```

生产环境应限定 PID 或 cgroup。

### 2. 把延迟变成分布

怀疑读取变慢时，可记录返回字节数并做直方图：

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_exit_read
/comm == "my-service" && args->ret > 0/
{ @bytes = hist(args->ret); }'
```

直方图比逐条日志更适合高频事件，输出量近似固定。需要耗时分布时，再用线程 ID 配对进入与返回事件。

### 3. 用结果验证假设

每个探针只回答一个问题，例如“请求线程是否在等待调度”。先写假设，再选挂载点；不支持就换观测点。

## 生产使用的边界

| 边界 | 做法 | 目的 |
| --- | --- | --- |
| 权限 | 受控 sudo，限制脚本目录 | 缩小高权限范围 |
| 范围 | 按 PID、进程或 cgroup 过滤 | 降低事件量 |
| 输出 | 计数、直方图、采样 | 避免打印风暴 |
| 时长 | 设置超时并记录窗口 | 便于回收审计 |
| 数据 | 路径和参数先脱敏 | 防止业务信息泄露 |

![生产观测中的采样、过滤与安全边界](/images/ebpf-bpftrace-observability/safety-boundaries.jpeg)

内核版本、发行版配置和容器权限都会影响探针。上线前用 `bpftrace -l 'tracepoint:syscalls:*'` 查看节点列表。容器看不到宿主机事件时，先确认观察层级和最小权限，不要直接开启全特权。

## 把排障沉淀成工具

验证假设后，把命令整理成脚本：写清适用内核和权限，参数化进程与时间窗口，输出固定列或 JSON，并给出退出码。并检查退出后没有残留 map 和后台进程。这样 eBPF 才能成为可审查、可复用的运行时观测工具。
