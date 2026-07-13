---
title: 文件描述符为何越用越少：用 /proc、lsof 与 strace 定位泄漏
date: 2026-07-14 03:06:59
tags:
  - Linux
  - 性能诊断
  - 可观测性
  - 稳定性
categories:
  - 工程实践
---

![Linux 文件描述符泄漏诊断](/images/linux-file-descriptor-leaks/cover.jpeg)

服务运行几天后突然报出 `Too many open files`，重启又恢复正常，通常不是“机器偶尔抽风”，而是某类文件描述符只打开、不释放。文件、Socket、管道和事件通知在 Linux 中都占用描述符；泄漏增长得很慢时，故障甚至会跨过多次常规监控窗口。定位它的关键，是先证明数量持续增长，再找出增长最快的资源类型，最后回到创建与关闭路径。

<!-- more -->

## 先理解限制在哪里

每个进程都有一张文件描述符表，`0`、`1`、`2` 通常对应标准输入、输出和错误。`open`、`socket`、`accept`、`pipe` 等调用会分配新编号，`close` 才归还。进程达到软限制时常见错误是 `EMFILE`；系统级文件表耗尽则可能出现 `ENFILE`。

```bash
# 当前 Shell 的软、硬限制
ulimit -Sn
ulimit -Hn

# 指定进程的限制与实时占用
grep -i 'open files' /proc/$PID/limits
find /proc/$PID/fd -maxdepth 1 -type l | wc -l
```

提高 `nofile` 只能延后故障，不能修复泄漏。还要注意容器内的限制可能来自运行时或编排配置，不能只看宿主机 Shell。

## 第一步：确认是泄漏还是压力

![进程文件描述符的组成与增长](/images/linux-file-descriptor-leaks/fd-table.jpeg)

连续采样比单个数字更有价值。下面的命令每五秒记录一次占用；若业务流量回落后数量仍只升不降，就值得继续追查。

```bash
PID=$(pgrep -n my-service)
while kill -0 "$PID" 2>/dev/null; do
  printf '%s ' "$(date '+%F %T')"
  find "/proc/$PID/fd" -maxdepth 1 -type l | wc -l
  sleep 5
done
```

| 现象 | 更可能的原因 |
| --- | --- |
| 随并发上升，流量回落后下降 | 正常连接或连接池容量 |
| 长时间呈单调上升 | 资源关闭路径遗漏 |
| 大量 `CLOSE_WAIT` | 对端已关闭，本进程未关闭 Socket |
| 大量带 `(deleted)` 的文件 | 文件已删除，但仍被进程持有 |
| 同类 `pipe`、`eventpoll` 持续增加 | 重复创建线程池或事件循环 |

## 第二步：给描述符分类

`/proc` 是最直接的现场。符号链接目标能区分普通文件、`socket:[inode]`、`pipe:[inode]` 和 `anon_inode`；`lsof` 则更便于查看协议、端口与已删除文件。

```bash
# 统计链接目标的类型；权限不足时需用进程所属用户执行
for fd in /proc/$PID/fd/*; do readlink "$fd"; done \
  | sed -E 's/^socket:\[[0-9]+\]$/socket/; s/^pipe:\[[0-9]+\]$/pipe/' \
  | sort | uniq -c | sort -nr | head

lsof -nP -p "$PID"
lsof -nP +L1 -p "$PID"   # 查找已删除但仍占空间的文件
ss -tanp                  # 观察 TCP 状态分布
```

不要把 `lsof` 的总行数直接当作打开描述符数：它还会列出当前目录、程序文本和内存映射。占用总数以 `/proc/$PID/fd` 为准，`lsof` 用来解释“它们是什么”。

## 第三步：追踪谁打开却没关闭

![文件描述符泄漏的分层诊断流程](/images/linux-file-descriptor-leaks/diagnostic-flow.jpeg)

已知资源类型后，可在复现环境用 `strace` 观察系统调用。优先追踪新启动的测试实例，附加线上进程会增加开销，也可能受 `ptrace` 权限限制。

```bash
strace -ff \
  -e trace=openat,close,socket,accept,accept4,pipe,pipe2,dup,dup2,dup3 \
  -o /tmp/fd.trace \
  ./my-service --config test.yml
```

将一次请求或任务作为边界，比较创建调用与 `close` 是否成对出现。不能机械地要求数量完全相等：监听 Socket、连接池和日志文件本来就会长驻；真正可疑的是同一路径在每次请求后净增一个资源。

代码修复应把资源生命周期绑定到明确作用域。例如 Python 的上下文管理器能覆盖正常返回和异常路径：

```python
def load_payload(path):
    with open(path, "rb") as stream:
        return stream.read()
```

在其他语言中使用 `defer`、`try/finally` 或框架提供的析构接口，原则相同：创建成功后立即安排清理，不把关闭动作散落在多个返回分支。

## 把复发挡在上线前

- 监控进程的 `open_fds`、`max_fds` 及其比值，并关注一段时间内的增长斜率。
- 按 Socket 状态、连接池、队列消费者等业务资源补充指标，避免只看到总量。
- 在稳定流量下做长时间循环测试，结束后等待连接回收，再比较描述符基线。
- 为 HTTP 响应体、数据库游标、临时文件和子进程管道建立统一关闭规范。
- 告警后保留 `/proc`、`lsof` 和网络状态快照，避免重启抹掉现场。

文件描述符泄漏并不神秘：数量曲线回答“是否在漏”，资源分类回答“漏的是什么”，系统调用与代码路径回答“在哪里漏”。按这个顺序收窄范围，比看到上限就调大 `nofile` 更快，也能把一次深夜重启变成可验证、可回归的修复。
