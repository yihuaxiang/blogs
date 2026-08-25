---
title: epoll 为什么会漏事件：LT、ET、非阻塞与读空循环
date: 2026-08-26 03:01:32
tags:
  - Linux
  - 网络编程
  - epoll
  - 性能优化
categories:
  - 工程实践
---

![epoll 事件循环与非阻塞 I/O](/images/linux-epoll-nonblocking-io/cover.jpeg)

服务从“每连接一个线程”改成 `epoll` 后，压测时却偶尔有连接停住，重启才恢复。问题通常不在 `epoll_wait()` 本身，而在程序混淆了“已经有数据”和“内核通知数据变化”这两件事。理解 LT、ET 的通知语义，并把 Socket、读取循环和业务状态机配套设计，才能避免看似随机的漏事件。

<!-- more -->

## epoll 只通知就绪，不替你完成 I/O

`epoll` 关注的是文件描述符是否可以执行某类操作，而不是一条消息是否完整。`EPOLLIN` 可能只代表 TCP 字节流中到达了一部分请求；一次 `read()` 也可能只取走内核缓冲区的一部分。因此事件循环需要先搬运字节，再由协议解析器判断是否收齐消息。

| 模式 | 未读完数据时 | 编程特点 |
| --- | --- | --- |
| LT（水平触发） | 下次等待仍会通知 | 容错更高，但重复通知较多 |
| ET（边沿触发） | 不保证再次通知 | 唤醒更少，必须一次处理到 `EAGAIN` |
| `EPOLLONESHOT` | 通知一次后暂时禁用 | 适合多线程接管，处理后必须重新激活 |

LT 像持续亮着的水位灯，只要还有水就提醒；ET 像水位变化的铃声，只在状态跨过边界时响。ET 下读取一次就返回业务层，缓冲区若仍有旧数据，又没有新数据到达，连接就可能永远等不到下一次铃声。

![LT 与 ET 的通知差异](/images/linux-epoll-nonblocking-io/trigger-modes.jpeg)

## ET 必须配合非阻塞与读空循环

把描述符设为 `O_NONBLOCK`，注册 `EPOLLIN | EPOLLET` 后，收到事件就循环读取。`read()` 返回 `EAGAIN` 或 `EWOULDBLOCK`，表示此刻已经搬空，可以安全回到 `epoll_wait()`；返回 0 表示对端完成关闭；`EINTR` 则应重试。

```c
static void drain_socket(int fd) {
    char buf[8192];

    for (;;) {
        ssize_t n = read(fd, buf, sizeof(buf));
        if (n > 0) {
            parser_feed(fd, buf, (size_t)n); // 解析器自己处理半包、粘包
            continue;
        }
        if (n == 0) {
            close_connection(fd);
            return;
        }
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return;

        close_connection(fd);
        return;
    }
}
```

监听 Socket 也遵守同一原则。ET 模式下一次通知可能对应多个已完成握手的连接，应循环调用 `accept4()`，直到它返回 `EAGAIN`。同时用 `SOCK_NONBLOCK | SOCK_CLOEXEC` 创建客户端 Socket，避免先 `accept()`、后改标志之间留下竞态窗口。

## 写事件要按需订阅

Socket 大多数时间都可写。如果在 LT 模式下永久监听 `EPOLLOUT`，`epoll_wait()` 会立即反复返回，让空闲进程也占满 CPU。更稳妥的做法是：先直接尝试 `write()`；数据全部写完时只监听读事件；遇到 `EAGAIN`，把剩余数据放入每连接的发送队列，再通过 `EPOLL_CTL_MOD` 增加 `EPOLLOUT`。之后持续发送到队列为空或再次遇到 `EAGAIN`，队列清空便取消写事件。

![非阻塞事件循环中的读写状态机](/images/linux-epoll-nonblocking-io/event-loop.jpeg)

这一规则也带来天然的背压边界：发送队列必须设置字节上限。慢客户端长期写不出去时，可以暂停读取、拒绝继续排队或关闭连接，不能让单个连接无限吞噬内存。

## 多线程处理要明确所有权

多个线程同时等待同一个连接时，最危险的不是重复唤醒，而是两个线程并发修改解析缓冲区和发送队列。常见做法是注册 `EPOLLONESHOT`：某个线程拿到事件后独占处理，完成读空、状态更新和兴趣事件计算，再用 `EPOLL_CTL_MOD` 带上 `EPOLLONESHOT` 重新激活。

重新激活不能散落在多个返回分支。建议把它放在统一的收尾路径，并在连接对象中保存代次或唯一 ID，避免旧任务晚到后误操作已关闭、编号又被内核复用的新描述符。

## 用系统调用追踪定位“卡住”

复现时不要只打印“收到事件”，还要记录描述符、事件位、每次读写字节数、队列长度和关闭原因。`strace` 可以确认事件循环究竟停在哪一步：

```bash
strace -ff -tt \
  -e trace=epoll_wait,epoll_ctl,accept4,read,write,close \
  -o /tmp/epoll.trace ./server
```

若最后一次 `read()` 返回正数而不是 `EAGAIN`，ET 读取很可能没有排空；若 `epoll_wait()` 不断返回 `EPOLLOUT`，检查是否忘记取消写兴趣；若线程长时间卡在 `read()` 或业务调用，检查描述符是否仍是阻塞模式，以及事件线程是否执行了耗时任务。还应统一处理 `EPOLLERR`、`EPOLLHUP` 和 `EPOLLRDHUP`：先消费剩余可读数据，遇到错误时用 `getsockopt()` 读取 `SO_ERROR`，再按协议决定是否关闭。

正确的 `epoll` 设计并不是多写一个 `EPOLLET` 标志，而是一组必须同时成立的约束：描述符非阻塞、读写持续到 `EAGAIN`、写事件按需开关、协议支持增量解析、多线程所有权明确。把这些约束落实到状态机和日志中，“偶发卡死”就会变成可复现、可定位的普通分支。
