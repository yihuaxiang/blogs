---
title: 容器隔离的另一半：用 Linux Namespaces 看懂进程、网络与文件系统边界
date: 2026-09-08 03:03:47
tags:
  - Linux
  - 容器
  - 系统原理
categories:
  - 工程实践
---

![Linux Namespaces 隔离示意封面](/images/linux-namespaces-isolation/cover.jpeg)

容器看起来像一台独立的小机器，但它并没有启动新的内核。进程、网络接口和挂载点之所以“看不见彼此”，关键靠 Linux Namespaces（命名空间）。它解释了容器里的 PID、`localhost` 和排障入口。

<!-- more -->

## Namespace 到底隔离了什么

Namespace 改变的是进程观察系统资源的视角。同一个内核可为不同进程提供不同视图；它不是资源限额，CPU、内存和 IO 配额仍由 cgroups 负责：

| 类型 | 隔离对象 | 常见用途 |
| --- | --- | --- |
| PID | 进程编号和进程树 | 让应用拥有独立的 PID 1 |
| NET | 网卡、路由、端口和防火墙 | 构造容器网络 |
| MNT | 挂载点视图 | 提供独立根文件系统 |
| UTS | hostname 和 domain name | 设置容器主机名 |
| USER | UID、GID 映射 | 在容器内使用 root，宿主机仍是普通用户 |

`CGROUP` 和 `TIME` 也属于 namespace；资源管理仍由 cgroup 控制器负责。

![PID、网络、挂载与用户命名空间的边界](/images/linux-namespaces-isolation/namespace-boundaries.jpeg)

## 用 unshare 做一个最小实验

`unshare` 可以让新启动的程序使用新的 namespace。下面只演示 PID、UTS 和 mount 视图，实验请在临时环境中执行：

```bash
sudo unshare --fork --pid --mount --uts bash
hostname isolated-demo
echo "inside: $$"
ps -ef
mount | head
exit
```

在新的 PID namespace 中，`bash` 会看到自己的 PID 树。`--fork` 很重要：调用 `unshare` 的原进程不会自动变成新空间的 PID 1。PID 1 还负责回收孤儿进程和转发信号。

网络 namespace 通常只有一个关闭状态的 `lo`，不会自动拥有宿主机网卡。容器运行时还要创建 veth pair、连接网桥并配置地址、路由和 DNS；只创建 `--net` 不代表端口能被外部访问。

## 排障时用 nsenter 看“同一个现场”

宿主机上看到的进程信息，可能和容器内执行命令的结果不同。先找目标进程的 PID，再用 `nsenter` 进入它的 namespace：

```bash
target=$(pgrep -n my-service)
sudo readlink /proc/$target/ns/{pid,net,mnt,uts,user}
sudo nsenter -t "$target" --pid --net --mount --uts --ipc sh
```

进入后执行 `ps`、`ip addr`、`mount`，结果才与目标进程一致。排障脚本最好显式列出类型；`/proc/<pid>/ns/` 的 inode 号可判断是否共享 namespace。

![使用 nsenter 进入运行中隔离环境排障](/images/linux-namespaces-isolation/nsenter-debugging.jpeg)

## 隔离边界不是安全边界

Namespace 提供的是视图隔离，不是完整安全边界。生产容器还应配合最小 capabilities、只读根文件系统、seccomp、SELinux/AppArmor，并避免共享宿主机 PID 或挂载 Docker socket。

可以用下面的清单验证一个容器的边界：

| 检查项 | 关注点 |
| --- | --- |
| PID | 是否意外共享宿主机进程树，应用是否处理 SIGTERM |
| NET | 监听地址、端口映射、路由和 DNS 是否符合预期 |
| MNT | 根文件系统是否只读，宿主机目录是否最小化挂载 |
| USER | 容器 root 是否映射到宿主机高权限 UID |
| 能力 | 是否删除不需要的 capability，是否限制系统调用 |

Namespace 把“程序能看到什么”变成可组合的内核边界。排查容器问题时，先确认目标进程的 PID、NET、MNT 视图，再判断是否受到 cgroups 限制，通常更快。
