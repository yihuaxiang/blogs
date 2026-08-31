---
title: 僵尸进程为什么 kill 不掉：从 waitpid 到 PID 1 的回收机制
date: 2026-09-01 03:04:27
tags:
  - Linux
  - 进程管理
  - 故障排查
  - 稳定性
categories:
  - 工程实践
---

![Linux 僵尸进程回收机制](/images/linux-zombie-process-reaping/cover.jpeg)

线上看到进程状态为 `Z`，第一反应往往是执行 `kill -9`。命令没有报错，僵尸却还在——这不是权限问题，而是目标已经结束运行，只剩父进程尚未领取的“退出回执”。理解 `fork`、退出与 `waitpid` 的配合，才能找到真正该修的进程。

<!-- more -->

## 僵尸究竟留下了什么

子进程退出后，内核会释放它的大部分内存、文件描述符等资源，但会暂时保留 PID、退出码和少量统计信息。父进程调用 `wait` 或 `waitpid` 取得结果后，这条记录才被彻底回收。退出与回收之间的状态就是僵尸，`ps` 中通常显示为 `Z` 或 `<defunct>`。

![子进程从运行到回收的生命周期](/images/linux-zombie-process-reaping/process-lifecycle.jpeg)

| 状态 | 是否仍执行代码 | 谁负责下一步 |
| --- | --- | --- |
| 运行中的子进程 | 是 | 调度器与进程自身 |
| 僵尸进程 | 否 | 父进程调用 `wait*` |
| 孤儿进程 | 可能仍在运行 | PID 1 或子收割器接管 |

孤儿与僵尸不是一回事：孤儿只是父进程先退出，自己仍可能正常工作；僵尸则已经无法执行任何指令。因此 `SIGKILL` 也无事可做。少量短暂僵尸通常只是父进程尚未调度到回收逻辑；持续增长才说明代码或进程管理有缺口。

## 先找到真正的责任方

先筛出状态以 `Z` 开头的进程，再沿 PPID 找父进程：

```bash
ps -eo pid=,ppid=,stat=,etime=,comm= \
  | awk '$3 ~ /^Z/ {print}'

# 将下面的 PID 换成僵尸进程号
grep -E '^(Name|State|Pid|PPid):' /proc/12345/status
pstree -aps 12345
```

`etime` 能区分瞬时现象与长期残留，`PPid` 则指向应执行回收的父进程。排查时还要观察数量是否增长：

```bash
watch -n 2 "ps -eo stat= | awk '\$1 ~ /^Z/ {n++} END {print n+0}'"
```

不要只盯着僵尸的命令名。父进程可能是应用服务器、任务执行器，也可能是容器中直接作为 PID 1 启动的业务程序。结合发布时间、子任务吞吐量和父进程日志，才能判断是某条异常路径漏掉 `wait`，还是父进程本身卡死。

## 用最小程序复现

下面的程序让子进程立即退出，而父进程故意不调用 `waitpid`。在 60 秒内查看进程树，就能看到 `Z` 状态：

```python
import os
import time

child_pid = os.fork()
if child_pid == 0:
    os._exit(0)

print(f"parent={os.getpid()}, child={child_pid}")
time.sleep(60)  # 故意遗漏 os.waitpid(child_pid, 0)
```

父进程结束后，残留子进程会被 PID 1 或配置为 subreaper 的进程接管，再由正常工作的回收者处理。线上直接杀父进程虽然可能让僵尸消失，却也会中断它承载的业务，因此只适合作为有排空和重启保障的止血方案，不是根治。

## 在代码里正确回收

父进程知道具体子进程时，应在合适位置调用阻塞式 `waitpid(pid, ...)`；同时管理多个异步子进程时，可收到 `SIGCHLD` 后循环执行非阻塞回收：

```c
#include <errno.h>
#include <signal.h>
#include <sys/wait.h>

static void reap_children(int signal_number) {
    int saved_errno = errno;
    while (waitpid(-1, NULL, WNOHANG) > 0) {}
    errno = saved_errno;
}

int main(void) {
    struct sigaction action = {0};
    action.sa_handler = reap_children;
    action.sa_flags = SA_RESTART | SA_NOCLDSTOP;
    sigemptyset(&action.sa_mask);
    sigaction(SIGCHLD, &action, NULL);
    /* 安装处理器后再创建子进程 */
}
```

循环很重要，因为一次 `SIGCHLD` 到达时可能已有多个子进程退出。复杂程序还可以把信号接入事件循环，统一维护 PID 与任务状态。使用高级语言的子进程库时，应遵循其生命周期 API 等待完成并消费结果；有些运行时会在内部回收，业务层仍不能只保存一个 PID 就不再管理。

![僵尸进程的诊断与修复路径](/images/linux-zombie-process-reaping/diagnostic-flow.jpeg)

## 容器里的 PID 1 陷阱

容器没有完整 init 进程时，业务程序可能直接成为 PID 1。若它会启动孙进程，却没有正确回收被重新托管的后代，僵尸就会在容器内积累。可根据运行环境启用轻量 init（例如容器运行时提供的 init 选项），或让入口程序明确承担信号转发与子进程回收职责。

修复后应做三项验证：反复触发创建子进程的正常与异常路径；确认任务结束后僵尸数回到基线；再验证容器停止时信号能传给真正的业务子进程。僵尸本身通常不消耗 CPU 和大块内存，但会占用 PID 表项，大量堆积最终可能让 `fork` 或创建线程失败。

## 一份处置顺序

1. 用 `ps` 确认 `Z` 状态、存活时长和数量趋势。
2. 沿 PPID 找到父进程，关联其日志、任务类型与发布变更。
3. 紧急止血时优先排空并重启父服务，不对僵尸反复 `kill -9`。
4. 修复所有退出分支的 `wait*`，并覆盖并发退出与异常启动场景。
5. 容器入口同时验证 PID 1 的回收和信号转发能力。

解决僵尸进程的关键，是把问题从“如何杀掉它”改成“父进程为何没有领取退出状态”。沿着状态、父子关系和回收代码逐层检查，才能让进程表恢复干净，也避免下一次任务高峰把 PID 空间悄悄耗尽。
