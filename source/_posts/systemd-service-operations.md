---
title: 把常驻进程交给 systemd：Unit、重启策略与最小权限
date: 2026-07-27 03:05:46
tags:
  - Linux
  - systemd
  - 运维
  - 安全
categories:
  - 工程实践
---

![systemd 管理常驻服务](/images/systemd-service-operations/cover.jpeg)

把程序放进后台运行并不难，难的是让它在机器重启后自动拉起、崩溃时按规则恢复、日志有处可查，还不能拿着不必要的 root 权限。与其维护 `nohup`、PID 文件和自制守护脚本，不如把进程生命周期交给 systemd，用一份 Unit 文件明确启动条件、资源边界和失败策略。

<!-- more -->

## 一份可维护的 Service Unit

假设 Node.js 服务部署在 `/opt/report-api/current`，运行数据写入 `/var/lib/report-api`，并已创建专用用户 `report-api`。可以新建 `/etc/systemd/system/report-api.service`：

```ini
[Unit]
Description=Report API
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=report-api
Group=report-api
WorkingDirectory=/opt/report-api/current
EnvironmentFile=-/etc/report-api/env
ExecStart=/usr/bin/node server.js

Restart=on-failure
RestartSec=3s
KillSignal=SIGTERM
TimeoutStopSec=20s

StateDirectory=report-api
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
```

`ExecStart` 的可执行文件使用绝对路径，避免 PATH 因登录环境不同而漂移。它不会自动经过通用 shell，管道、重定向等复杂逻辑应放进一个可测试的脚本，而不是塞进 Unit。`EnvironmentFile` 前的 `-` 表示文件缺失时不阻止启动；若配置是必需的，应去掉它，让错误尽早暴露。

修改后按顺序加载并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now report-api.service
sudo systemctl status report-api.service
```

`daemon-reload` 只让 systemd 重新读取配置，不会自动重启正在运行的服务。确认配置无误后，还需显式执行 `systemctl restart report-api` 才能让新参数生效。

## 重启策略不是无限重试

![服务启动、失败恢复与日志链路](/images/systemd-service-operations/service-lifecycle.jpeg)

`Restart=on-failure` 会在非零退出、异常信号或超时等失败场景下重启，但正常退出不会被当成故障。配合 `RestartSec` 可以避免紧密循环；`StartLimitIntervalSec` 与 `StartLimitBurst` 则限制单位时间内的启动次数，防止错误配置持续消耗 CPU、打爆数据库或刷满日志。

| 配置 | 解决的问题 | 使用原则 |
| --- | --- | --- |
| `Restart=on-failure` | 意外退出后恢复 | 程序必须用退出码表达结果 |
| `RestartSec=3s` | 失败后立刻重试 | 给依赖和机器留出恢复时间 |
| 启动频率限制 | 永久崩溃循环 | 达到上限后告警并人工检查 |
| `TimeoutStopSec` | 停止过程无限等待 | 应大于应用正常清理所需时间 |

守护进程仍要正确处理 `SIGTERM`：停止接收新任务、关闭连接并退出。systemd 能负责发送信号和执行超时兜底，却无法替应用完成业务收尾。依赖数据库或远程 API 时，应用也应自行做有上限的重连；`network-online.target` 只表达启动关系，不保证远端服务一定可用。

## 用最小权限缩小故障半径

不要因为权限问题就把 `User` 改成 root。`ProtectSystem=strict` 将大部分文件系统设为只读，`ProtectHome=true` 隔离用户目录，`PrivateTmp=true` 给服务独立的临时目录，`NoNewPrivileges=true` 阻止进程通过执行文件获得额外权限。`StateDirectory=report-api` 会准备服务专用的可写状态目录，因此只读保护不妨碍正常落盘。

这些选项需要逐项验证，尤其是会启动子进程、加载动态模块或访问特殊设备的程序。可以先查看安全评估，再根据真实访问需求放行，而不是一次关闭所有保护：

```bash
systemd-analyze security report-api.service
journalctl -u report-api.service -f
```

应用日志直接写标准输出和标准错误，journal 会统一收集。排障时常用以下命令：

```bash
journalctl -u report-api --since "30 minutes ago"
systemctl show report-api -p MainPID -p ActiveState -p NRestarts
systemctl cat report-api
```

最后一条会显示 systemd 实际加载的 Unit 与覆盖配置，能快速发现“明明改了文件却没生效”或存在 drop-in 的情况。

## 上线前检查

- 使用专用低权限用户，写目录和监听端口均按需授权。
- 手动制造一次非零退出，确认重启间隔、频率限制与告警符合预期。
- 执行 stop 和 restart，确认程序收到 `SIGTERM` 后能在超时内收尾。
- 重启测试机，验证 `enable` 后的开机启动与依赖关系。
- 用 journal 检查启动参数、退出码和失败原因是否足够排障。

一份好的 Unit 不是“能把进程拉起来”就结束，而是把运行身份、启动顺序、失败恢复、停止预算和可写范围变成可审查的配置。这样服务的行为不再依赖某次登录会话或某个人记得执行脚本，机器重启和程序崩溃也会沿着同一套规则处理。
