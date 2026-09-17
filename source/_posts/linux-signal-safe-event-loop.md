---
title: Linux 信号处理别塞业务逻辑：异步安全、Self-Pipe 与 signalfd
date: 2026-09-18 03:02:10
tags:
  - Linux
  - 系统编程
  - 信号
  - 事件循环
categories:
  - 工程实践
---

![Linux 信号进入事件循环](/images/linux-signal-safe-event-loop/cover.jpeg)

进程收到 `SIGTERM` 后要停止接流量，收到 `SIGHUP` 后要重载配置，看起来只需注册两个回调。然而信号处理函数会在不可预测的指令之间打断程序；若在里面加锁、分配内存或写日志，偶发死锁和状态损坏便可能随之而来。稳妥的思路是：处理函数只负责通知，把真正的工作交还主循环。

<!-- more -->

## 信号处理函数为什么特殊

普通函数由程序主动调用，信号处理函数却可能打断任意线程。被打断的代码也许正持有 `malloc` 或日志库内部的锁，此时处理函数再次调用同一套设施，就会等待一把只有自己返回后才可能释放的锁。

POSIX 只保证一小组函数是异步信号安全的，例如 `write`、`close` 和 `_exit`。`printf`、`malloc`、大多数日志接口以及互斥锁都不在其中。把处理函数写得“很短”并不等于安全，关键是它调用了什么、读写的数据能否在异步打断下保持正确。

还有两个容易忽略的事实：普通信号通常不会像消息队列一样逐个排队，同一种信号快速到达多次，程序可能只观察到一次；阻塞中的系统调用也可能以 `EINTR` 返回，是否自动重启取决于调用和信号安装方式。因此，信号适合表达“状态需要检查”，不适合承载精确计数或业务数据。

## 三种方案怎么选

| 方案 | 处理函数做什么 | 适用场景 | 注意点 |
| --- | --- | --- | --- |
| `sig_atomic_t` 标志 | 设置标志位 | 主循环本来就会周期醒来 | 休眠可能延迟响应 |
| Self-Pipe | 向管道写一个字节 | `select`、`poll`、`epoll` 主循环 | 管道满时只保留“有事件”语义 |
| `signalfd` | 无传统处理函数 | Linux 专用事件循环 | 所有相关线程都要正确屏蔽信号 |

单线程小程序可以使用 `volatile sig_atomic_t` 标志，并在正常控制流里检查。事件循环若可能无限阻塞，标志位本身无法唤醒它，Self-Pipe 更合适。已经绑定 Linux、希望读取信号编号和发送者信息时，可选择 `signalfd`。

![信号处理函数只通知主循环](/images/linux-signal-safe-event-loop/signal-flow.jpeg)

## 用 Self-Pipe 唤醒主循环

Self-Pipe 的做法是创建一条非阻塞管道，把读端加入 `poll`；处理函数只向写端写入一个字节。即使写入因管道已满而得到 `EAGAIN`，也说明读端已经可读，唤醒目的仍然达成。

```c
static int signal_fd[2];

static void on_signal(int signo) {
    unsigned char value = (unsigned char)signo;
    ssize_t ignored = write(signal_fd[1], &value, 1);
    (void)ignored;
}

int main(void) {
    pipe2(signal_fd, O_NONBLOCK | O_CLOEXEC);

    struct sigaction action = {0};
    action.sa_handler = on_signal;
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGHUP, &action, NULL);

    /* 将 signal_fd[0] 注册进 poll/epoll；可读后持续读到 EAGAIN，
       再在正常上下文执行退出或重载。 */
}
```

生产代码还应检查初始化调用的错误，并在安装处理函数前完成管道创建。读取时一次性排空所有字节，但不要把每个字节都当成可靠计数：管道可能合并“需要处理”的通知。若必须区分退出与重载，可在排空后检查独立标志位；标志表达状态，管道负责唤醒。

## 用 signalfd 统一事件来源

`signalfd` 能把信号变成文件描述符上的结构化记录，直接纳入 `epoll`。正确顺序是先用 `pthread_sigmask` 屏蔽目标信号，再创建 `signalfd`，随后从它读取 `signalfd_siginfo`。多线程程序尤其要在创建工作线程之前设置屏蔽掩码，让新线程继承；否则信号可能被投递到未屏蔽的线程，绕过 `signalfd`，甚至触发默认动作。

```c
sigset_t mask;
sigemptyset(&mask);
sigaddset(&mask, SIGTERM);
sigaddset(&mask, SIGHUP);
pthread_sigmask(SIG_BLOCK, &mask, NULL);

int fd = signalfd(-1, &mask, SFD_NONBLOCK | SFD_CLOEXEC);
/* 把 fd 加入 epoll，在正常事件分支中 read 并处理。 */
```

`signalfd` 简化了 Linux 服务的控制流，但它不是跨平台接口。需要兼容其他 Unix 系统时，Self-Pipe 往往更易移植。

## 把退出做成状态机

收到 `SIGTERM` 不应立即在处理函数里关闭所有资源。主循环可以按顺序进入“停止接收新任务、等待进行中任务、到期强制结束、刷新必要状态、退出”的阶段。再次收到退出信号时，可缩短宽限期或直接进入强制阶段。`SIGHUP` 重载也应先解析并校验新配置，成功后再原子替换，避免半更新状态。

测试时不要只按一次 Ctrl+C。应覆盖主循环空闲和繁忙时的信号、连续多个同类信号、退出期间再次退出、系统调用返回 `EINTR`，以及多线程下的投递行为。信号处理最可靠的边界不是“回调足够聪明”，而是让异步上下文只发出最小通知，让所有复杂操作回到可测试、可排序的正常控制流。
