---
title: systemd Socket Activation：让服务按需启动并平滑接管连接
date: 2026-11-23 03:01:53
tags:
  - Linux
  - systemd
  - 网络编程
  - 运维
categories:
  - 工程实践
---

![systemd Socket Activation 流程示意图](/images/systemd-socket-activation/cover.jpeg)

systemd 的 Socket Activation 可以先监听端口，把连接暂存在内核队列，真正有请求时再启动服务进程。服务重启时也能保留新连接。

<!-- more -->

## 两个 Unit，各司其职

Socket Activation 由 `.socket` 和 `.service` 两个 Unit 组成。`.socket` 负责创建监听套接字，`.service` 负责处理已建立的连接；两者文件名相同，systemd 会自动关联。

```ini
# demo.socket
[Unit]
Description=Demo listening socket

[Socket]
ListenStream=127.0.0.1:9000
Backlog=128

[Install]
WantedBy=sockets.target
```

```ini
# demo.service
[Unit]
Description=Demo server
Requires=demo.socket

[Service]
ExecStart=/usr/local/bin/demo-server --fd 3
StandardInput=socket
Restart=on-failure
```

## 队列如何保护重启窗口

![监听套接字与内核连接队列](/images/systemd-socket-activation/queue.jpeg)

连接先进入监听套接字的 accept 队列。服务进程退出后，`.socket` 仍可保持监听，systemd 随后按需拉起新的 `.service`。它不是无限缓冲：`Backlog` 过小会在突发流量时拒绝连接，过大则可能掩盖处理能力不足。

```bash
systemctl enable --now demo.socket
systemctl status demo.socket
ss -lntp | grep 9000
journalctl -u demo.socket -u demo.service -f
```

部署时先更新二进制，再重启 `demo.service`；监听端口无需释放，新的连接会由新进程接管。

## 常见边界与陷阱

TCP 使用 `ListenStream`，UDP 使用 `ListenDatagram`；多实例场景才考虑 `Accept=yes`，并提前评估进程数量。不要让启动脚本再次执行 `bind` 或端口探测，否则会与 systemd 创建的资源冲突。修改 `.socket` 后要运行 `systemctl daemon-reload`，再重启 socket；只重启 service 不会应用新端口。

## 把安全边界写进 Unit

![systemd 服务的资源与权限边界](/images/systemd-socket-activation/hardening.jpeg)

Socket Activation 解决的是监听生命周期，不等于自动完成安全加固。服务 Unit 应设置专用用户和资源限制：

```ini
[Service]
User=demo
Group=demo
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
MemoryMax=256M
LimitNOFILE=4096
```


