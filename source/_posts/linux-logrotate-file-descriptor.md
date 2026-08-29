---
title: 日志轮转不只是改名：读懂 logrotate、inode 与文件重开
date: 2026-08-30 03:02:24
tags:
  - Linux
  - 日志
  - 运维
  - 稳定性
categories:
  - 工程实践
---

![Linux 日志轮转与文件重开](/images/linux-logrotate-file-descriptor/cover.jpeg)

线上日志持续增长时，直接删除大文件未必能释放磁盘，简单改名也未必能让进程写入新文件。原因在于 Linux 进程持有的是指向文件对象的描述符，而不是一条会自动跟随变化的路径。把 `logrotate` 配置写对，关键不是记住几个参数，而是明确谁负责切换文件、进程何时重开描述符，以及怎样证明空间真的被回收。

<!-- more -->

## 为什么改名后还在写旧文件

目录中的文件名可以理解为 inode 的入口。进程打开日志后，文件描述符直接引用对应的文件对象；此后即使把 `app.log` 重命名为 `app.log.1`，原描述符仍然有效，写入会继续落到 `app.log.1`。如果旧文件被删除但描述符没有关闭，它会变成“已删除但仍打开”的文件，磁盘空间要等最后一个引用释放后才归还。

```bash
PID=$(pgrep -n my-service)
ls -li /var/log/my-service/app.log*
lsof -nP +L1 -p "$PID"
```

`ls -li` 用来比较路径对应的 inode，`lsof +L1` 用来发现链接数小于 1、却仍被进程占用的文件。看到日志名称已经变化，不能据此判断轮转完成；还要确认进程的新描述符指向新文件。

![日志路径、inode 与进程描述符的关系](/images/linux-logrotate-file-descriptor/inode-flow.jpeg)

## 两种轮转策略如何选择

| 策略 | 工作方式 | 优点 | 风险与适用边界 |
| --- | --- | --- | --- |
| 重命名后重开 | 改名旧文件，创建新文件，通知进程重开 | 切换边界清晰，适合长期运行的服务 | 程序必须支持可靠的 reload 或日志重开信号 |
| `copytruncate` | 复制内容后，在原 inode 上截断 | 进程无需重开描述符 | 复制与截断之间存在竞态，少量新日志可能丢失 |

优先选择“重命名后重开”。`copytruncate` 是兼容无法重开日志的旧程序的退路，不应仅因为配置简单就默认启用。对于写入量很大的日志，它还会带来完整复制的 I/O 成本。

## 一份可审查的 logrotate 配置

下面的配置按天轮转，并在单个文件超过 200 MiB 时提前轮转。轮转后保留 14 份，压缩延后一轮，给进程切换旧文件留出余量。

```conf
/var/log/my-service/*.log {
    daily
    maxsize 200M
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    create 0640 app app
    su app app
    sharedscripts

    postrotate
        /usr/bin/systemctl kill -s HUP my-service.service
    endscript
}
```

`create` 会为原路径创建权限明确的新文件，`su` 指定轮转时使用的用户和组，避免依赖目录的偶然权限。`sharedscripts` 让通配符匹配多个日志时只执行一次 `postrotate`。这里的 `HUP` 只是示例：必须先查清服务对信号的约定；有些程序使用专用命令，有些收到 `HUP` 会重新加载全部配置，甚至直接退出。

如果应用把日志写到标准输出，通常应由容器运行时或平台统一轮转，不要再让应用内部和宿主机同时管理同一份日志。轮转责任只能有一个明确所有者。

## 上线前怎样验证

先用调试模式检查匹配路径、权限和策略，调试模式不会实际轮转：

```bash
sudo logrotate -d /etc/logrotate.d/my-service
```

需要验证真实切换时，在测试环境写入带时间戳的连续日志，再强制执行一次轮转：

```bash
sudo logrotate -f /etc/logrotate.d/my-service
ls -li /var/log/my-service/app.log*
lsof -nP -p "$(pgrep -n my-service)" | grep '/var/log/my-service/'
```

检查应覆盖四件事：新文件的属主和权限正确；服务仍持续写入；新日志进入新的 inode；旧文件不再增长。随后确认压缩和保留数量符合预期，并查看 `logrotate` 自身由定时器或 cron 触发时的错误记录。不要只手工运行成功一次，因为自动任务的用户、环境和权限可能不同。

## 故障时的排查顺序

当磁盘告警或日志停止更新时，可以按以下顺序收窄问题：

1. 用 `df -h` 判断文件系统是否满，用 `du` 判断可见文件是否真的占满；两者差距很大时，优先查已删除但仍打开的文件。
2. 用 `lsof +L1` 找到占用旧 inode 的进程，不要反复删除路径。
3. 检查服务是否收到重开信号、是否有权限创建或打开新日志。
4. 核对轮转状态文件和自动任务日志，确认规则是否实际命中。
5. 最后才调整保留份数、压缩策略和大小阈值，并结合日志增长速度估算容量。

稳定的日志轮转是一份跨进程契约：`logrotate` 负责改名、创建、压缩和清理，应用负责及时关闭旧描述符并打开新路径，监控负责验证两者真的完成交接。围绕 inode 和描述符观察现场，才能避免“文件删了，磁盘却没回来”这类看似反常、其实完全符合 Linux 语义的故障。
