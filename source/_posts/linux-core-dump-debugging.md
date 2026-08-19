---
title: 程序崩溃别只重启：用 Core Dump 与 GDB 保全 Linux 现场
date: 2026-08-20 03:03:37
tags:
  - Linux
  - GDB
  - 故障排查
  - 稳定性
categories:
  - 工程实践
---

![Linux Core Dump 崩溃诊断](/images/linux-core-dump-debugging/cover.jpeg)

线上进程收到 `SIGSEGV` 后退出，日志往往只留下“Segmentation fault”。立即重启能恢复服务，却也抹掉了最有价值的寄存器、调用栈与线程状态。Core Dump 是进程崩溃时的内存快照；把它与原始可执行文件、调试符号配对，就能从“它挂了”推进到“哪个线程、哪一行、哪个参数导致了崩溃”。

<!-- more -->

## Core Dump 能回答什么

Core 文件通常保存崩溃信号、寄存器、线程栈和部分内存映射。它适合分析非法访存、断言失败、主动 `abort` 等终止现场，但不是完整的执行录像，无法直接还原崩溃前每一步操作。

| 现象 | 常见信号 | 优先检查 |
| --- | --- | --- |
| 非法地址访问 | `SIGSEGV` | 故障指令、空指针、越界访问 |
| 主动终止或断言失败 | `SIGABRT` | 触发断言的栈帧、错误分支 |
| 非法指令 | `SIGILL` | 二进制与 CPU 是否匹配、代码是否损坏 |
| 算术异常 | `SIGFPE` | 整数除零、启用捕获的浮点异常 |

Core 可能包含令牌、用户数据和明文密钥，应当按敏感生产数据限制访问、传输和保留周期，不能随手上传到公共工单。

## 先制造一个可控现场

![从进程崩溃到定位源码的分析流程](/images/linux-core-dump-debugging/crash-flow.jpeg)

先在隔离的测试环境确认整条链路。下面的程序会稳定触发空指针写入；编译时加入 `-g` 保留调试信息，并暂时关闭优化，便于观察变量。

```c
// crash.c
#include <stdio.h>

static void write_value(int *target) {
    *target = 42;
}

int main(void) {
    int *ptr = NULL;
    write_value(ptr);
    return 0;
}
```

```bash
gcc -g -O0 -fno-omit-frame-pointer -o crash crash.c
ulimit -c unlimited
cat /proc/sys/kernel/core_pattern
./crash
```

`ulimit -c` 控制当前 Shell 及其子进程允许生成的 Core 大小。`core_pattern` 若是文件模板，Core 会按模板落盘；若以管道符 `|` 开头，内核会把现场交给用户态收集器，因此当前目录里找不到 `core` 并不代表没有采集。

在使用 systemd-coredump 的系统上，可以先查询记录，再导出需要分析的文件：

```bash
coredumpctl list crash
coredumpctl info crash
coredumpctl dump crash --output=crash.core
```

不同发行版和容器的收集方式可能不同。不要直接套用一条修改 `core_pattern` 的命令；先读取限制、内核配置和服务管理器设置，弄清 Core 最终由谁接收。

## 用匹配的二进制还原栈帧

![可执行文件、调试符号与 Core 文件配对](/images/linux-core-dump-debugging/symbol-match.jpeg)

分析时必须使用产生 Core 的那一版可执行文件。即使源码相同，重新编译也可能改变地址布局；共享库和独立调试符号也应来自同一构建制品。

```bash
gdb ./crash ./crash.core

# 进入 GDB 后
(gdb) bt full
(gdb) thread apply all bt full
(gdb) frame 0
(gdb) info args
(gdb) info locals
(gdb) list
(gdb) print target
```

先看 GDB 报告的终止信号与故障线程，再用 `bt full` 查看当前线程；多线程服务则用 `thread apply all bt full` 判断是单线程越界，还是锁、回调与共享状态共同造成的问题。切换到可疑栈帧后再检查参数和局部变量，避免只盯着最顶层的崩溃函数——内存可能早在上游就被破坏。

## 没有可读栈时逐层排查

| 问题 | 检查方式 | 处理方向 |
| --- | --- | --- |
| 根本没有 Core | `ulimit -Sc`、`core_pattern`、服务日志 | 检查大小限制、收集器、目录权限和容器边界 |
| 栈里只有地址或 `??` | `file`、`readelf -S` | 保存未剥离符号或单独的 debug 文件 |
| GDB 提示文件不匹配 | 比对 Build ID、制品哈希 | 从同一次构建取回二进制和库 |
| 栈帧明显断裂 | 检查优化级别与栈破坏迹象 | 保留帧指针，结合反汇编和寄存器分析 |

生产构建不必为了调试完全关闭优化。更稳妥的做法是发布经过剥离的运行文件，同时把带符号制品按 Build ID 归档；故障发生后再离线配对。这样既控制体积，也避免临时重编译造成地址不一致。

## 把崩溃诊断做成固定流程

- 在服务配置中明确 Core 大小限制，并验证重启或部署后仍然生效。
- 为二进制、共享库、调试符号和源码版本记录同一个构建标识与哈希。
- 监控异常退出信号和短时间重复崩溃，避免自动拉起形成无限重启循环。
- 将 Core 存在受控目录，设置最小读取权限、容量上限和自动清理策略。
- 在预发布环境定期触发一次受控崩溃，验证“采集、导出、符号化、定位源码”全链路。

Core Dump 的价值不在于留下一个大文件，而在于保住无法重现的瞬间。先确认谁负责采集，再保存同构建的二进制与符号，最后按“信号—线程—栈帧—变量”的顺序收窄范围，线上崩溃才能从一次重启事故变成可定位、可验证的修复任务。
