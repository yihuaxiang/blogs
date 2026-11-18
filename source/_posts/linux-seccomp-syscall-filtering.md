---
title: Linux Seccomp 系统调用过滤：给进程加一道可审计的边界
date: 2026-11-19 03:01:09
tags:
  - Linux
  - 安全
  - seccomp
categories:
  - 工程实践
![Linux seccomp 沙箱示意](/images/linux-seccomp-syscall-filtering/cover.jpeg)

很多服务只需读取配置、处理请求，却拥有打开文件、创建进程等能力。seccomp 在进入内核的边界上限制系统调用，把进程能力变成可审计的契约。它需要观测、测试和逐步收紧。

<!-- more -->

## seccomp 拦截什么

程序通过系统调用进入内核，seccomp 根据调用号和参数决定放行、记录、返回错误或终止进程。常用动作是 `SCMP_ACT_ERRNO`，调用被拒绝并返回 `EPERM`；对高风险服务可用 `SCMP_ACT_KILL_PROCESS` 立即结束进程。libseccomp 会把规则编译为 BPF，应用无需手写指令：

```c
scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_ERRNO(EPERM));
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(read), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(write), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(exit), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(exit_group), 0);
seccomp_load(ctx);
seccomp_release(ctx);
```

真实程序还要补齐内存映射、信号、线程和网络调用，并在初始化完成后加载。

## 从观测到白名单

不要凭经验复制调用列表。先在测试环境记录，再按业务路径收敛：

| 阶段 | 默认动作 | 目标 |
| --- | --- | --- |
| 观测 | `SCMP_ACT_LOG` | 收集启动、探活和请求路径 |
| 试运行 | `SCMP_ACT_ERRNO(EPERM)` | 暴露遗漏调用及错误处理 |
| 收紧 | `SCMP_ACT_KILL_PROCESS` | 阻止明确不应出现的行为 |

![系统调用白名单的放行与拒绝路径](/images/linux-seccomp-syscall-filtering/filter-flow.jpeg)

观测要覆盖升级、异常处理和关闭，并按版本和功能开关分组。新增调用时写清用途和回归场景。

## 线程、子进程与部署

过滤器通常随线程继承。若要创建子进程，必须验证 `clone`、`fork`、`execve` 等调用；懒加载和 DNS 解析也可能在收紧后失败。

规则与容器运行时 profile 叠加，任一层拒绝都会生效；应随镜像版本化。

## 排障与回滚

![排查 seccomp 拒绝调用的调试场景](/images/linux-seccomp-syscall-filtering/debug.jpeg)

遇到 `EPERM` 或进程被杀，先区分 seccomp、文件权限和 LSM，再结合日志与 `strace` 找到首次被拒绝的调用：

1. 记录镜像版本和 profile 校验值。
2. 临时改为日志或 `ERRNO`，复现最小请求。
3. 判断调用属于必要路径还是异常路径。
4. 只增加最小规则并回归关键流程，保留上一版 profile 以便回滚。

先观测再收敛，并把规则、测试和部署版本绑定，seccomp 才能成为长期可维护的安全边界。
