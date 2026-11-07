---
title: Linux Capabilities：把 root 权限拆成可审计的小权限
date: 2026-11-08 03:04:58
tags:
  - Linux
  - 安全
  - 运维
categories:
  - 工程实践
---

![Linux Capabilities 权限拆分示意](/images/linux-capabilities/cover.jpeg)

一个只需要监听 80 端口的服务，如果直接以 root 启动，也同时拥有读取敏感文件、修改网络和影响其他进程的能力。Linux Capabilities 把 UID 0 的特权拆成小集合，只保留任务所需权限。

<!-- more -->

## 先理解权限边界

UID 为 0 时，内核会跳过很多检查。Capabilities 将特权拆成独立项：

| 能力 | 用途 | 需要注意 |
| --- | --- | --- |
| `CAP_NET_BIND_SERVICE` | 绑定 1024 以下端口 | 不代表能改防火墙 |
| `CAP_NET_ADMIN` | 配置网卡、路由 | 权限范围较宽 |
| `CAP_CHOWN` | 修改文件属主 | 可能改变目录边界 |
| `CAP_SYS_PTRACE` | 检查其他进程 | 可能接触敏感数据 |

能力仍要配合文件权限和沙箱。每个线程有 effective、permitted 等集合：前者决定当前调用，后者是可用上限。进程可以删除能力，却不能凭空增加。

用下面的命令观察实际状态：

```bash
capsh --print
getpcaps 1234
```

![Capabilities 集合与进程边界](/images/linux-capabilities/capability-model.jpeg)

## 给文件和服务最小授权

文件能力适合确实需要某项特权的二进制：

```bash
sudo setcap 'cap_net_bind_service=+ep' /opt/demo/bin/server
getcap /opt/demo/bin/server
sudo setcap -r /opt/demo/bin/server
```

`+ep` 同时设置 permitted 和 effective。替换文件后要重新执行 `getcap`。不要把能力加到 Python、Node 这类通用解释器上，否则所有脚本都会继承权限；更稳妥的是让反向代理监听 80，应用运行在高端口。

systemd 可以把上限写入 Unit，并阻止进程再次提权：

```ini
[Service]
User=app
ExecStart=/opt/demo/bin/server
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
```

`CapabilityBoundingSet` 是允许范围，`AmbientCapabilities` 让非 root 进程继承指定能力。重启服务后检查 `/proc/$PID/status` 中的 `Cap` 字段。

容器也应先丢弃全部能力：

```bash
docker run --cap-drop=ALL \
  --cap-add=NET_BIND_SERVICE \
  --user 10001:10001 my-server
```

`--privileged` 会放宽太多边界，只用于排障。

![能力配置的运维检查](/images/linux-capabilities/operations-checklist.jpeg)

## 上线检查清单

1. 先用普通用户运行，确认失败点确实需要某项能力。
2. 一次只添加一项，重启后验证流程。
3. 检查 effective/permitted 集合，确认启动器没有扩大权限。
4. 把 `setcap`、systemd 和容器配置纳入版本控制。
5. 用日志或 `strace` 验证权限边界。

Capabilities 不能替代沙箱，却能把 root 缩成可审计的权限。
