---
title: Linux io_uring 入门：用提交队列和完成队列组织异步 I/O
date: 2026-11-07 03:01:46
tags:
  - Linux
  - io_uring
  - 性能
categories:
  - Linux
---

![Linux io_uring 异步 I/O 示意图](/images/linux-io-uring/cover.jpeg)

当服务同时处理大量文件、网络和数据库操作时，线程常常在等待 I/O。`io_uring` 把请求提交和结果收集放进共享的环形队列，让应用减少系统调用次数，并把“等待完成”从阻塞线程改成可批量消费的事件流。理解它的队列模型，才能判断它适合什么场景。

<!-- more -->

## 先看懂两条队列

`io_uring` 由提交队列（SQ）和完成队列（CQ）组成。应用先准备一个提交队列条目（SQE），写入操作类型、文件描述符、缓冲区和用户数据，再把 SQE 放入 SQ。内核取走请求并执行，完成后把结果写入 CQE，应用从 CQ 读取返回值。

![io_uring 提交队列与完成队列](/images/linux-io-uring/queues.jpeg)

多个请求可以一次提交，多个结果也可以一次收割。队列并不代表操作一定并行，实际并行度仍受设备和队列深度限制。

## 一个请求经过哪些阶段

一次读取通常经历准备缓冲区、提交 SQE、收割 CQE、处理结果四步。`user_data` 应指向稳定的请求上下文，完成时用它找回文件名、缓冲区和下一步动作。

![io_uring 请求生命周期](/images/linux-io-uring/lifecycle.jpeg)

伪代码可以写成这样：

```c
struct request {
    int fd;
    void *buf;
    size_t len;
    int state;
};

sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, req->fd, req->buf, req->len, 0);
io_uring_sqe_set_data(sqe, req);
io_uring_submit(&ring);

io_uring_wait_cqe(&ring, &cqe);
req = io_uring_cqe_get_data(cqe);
if (cqe->res >= 0) {
    consume(req->buf, cqe->res);
} else {
    handle_errno(-cqe->res);
}
io_uring_cqe_seen(&ring, cqe);
```

完成结果中的 `res` 是操作返回值，失败时通常是负的 errno。不要只看系统调用是否成功：短读、EOF、取消和部分写入都必须进入明确的状态机。

## 阻塞、轮询和批量提交怎么选

最简单的模式是 `io_uring_wait_cqe`，没有完成项时睡眠，适合后台任务和普通服务。需要降低尾延迟时，可以批量准备多个 SQE，再一次调用 `io_uring_submit`。批量大小应来自压测，队列过深会增加内存占用和排队延迟。


| 场景 | 起步策略 | 主要观察指标 |
| --- | --- | --- |
| 文件批处理 | 批量提交 + 阻塞收割 | 吞吐、队列深度 |
| 网络服务 | 非阻塞 socket + CQ 事件循环 | P99 延迟、CQ 堆积 |
| 极低延迟 | 小批量 + 受控轮询 | CPU 占用、尾延迟 |

## 生命周期和错误边界

异步请求提交后，缓冲区、`iovec`、用户上下文和文件描述符必须保持有效，直到对应 CQE 被消费。栈上临时数组、提前释放的对象和复用中的缓冲区，是最常见的悬空引用来源。可以让请求对象拥有这些资源，并在 CQE 收割后统一释放。

关闭文件也要谨慎：一个请求仍在使用 fd 时，不要让编号被快速复用给另一个文件。服务退出时先停止接收新任务，再等待或取消未完成请求，最后销毁 ring。取消并不等于立即完成，仍要消费最终 CQE，避免泄漏上下文。

## 什么时候不必使用 io_uring


