---
title: 用 Unix Domain Socket 做本机 IPC：协议、权限与生命周期清单
date: 2026-08-28 03:03:25
tags:
  - Linux
  - IPC
  - 网络编程
categories:
  - 工程实践
---

![Unix Domain Socket 本机通信](/images/unix-domain-socket-local-ipc/cover.jpeg)

同一台机器上的反向代理、守护进程与业务服务，经常不需要暴露 TCP 端口。Unix Domain Socket（UDS）用文件系统路径标识端点，既能复用成熟的 Socket 编程模型，又能借助目录权限缩小访问范围。但“把端口换成路径”只是开始，消息边界、残留文件和背压仍需明确设计。

<!-- more -->

## 先选对 Socket 类型

UDS 不经过 IP 路由，但数据仍会进入内核缓冲区，也仍可能发生阻塞、短读和对端提前关闭。常见类型的语义不同：

| 类型 | 消息边界 | 适合场景 | 主要注意点 |
| --- | --- | --- | --- |
| `SOCK_STREAM` | 不保留 | 请求响应、持续连接 | 必须自行分帧，兼容性最好 |
| `SOCK_DGRAM` | 保留 | 本机事件、短消息 | 要限制消息大小并处理缓冲区满 |
| `SOCK_SEQPACKET` | 保留且有序 | 需要连接和整包语义 | 使用前确认目标系统支持 |

多数服务优先选 `SOCK_STREAM`。它的 `recv()` 返回“当前可读的字节”，不保证一次调用正好得到一次 `send()` 的内容。JSON 本身也没有天然边界，因此应选择换行分隔、固定长度或“长度头 + 正文”等协议。

下面用 4 字节网络序长度头读取一帧，并在分配内存前限制大小：

```python
import struct

MAX_FRAME = 1024 * 1024

def recv_exact(conn, size):
    chunks = []
    while size:
        chunk = conn.recv(size)
        if not chunk:
            raise EOFError("peer closed mid-frame")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)

def recv_frame(conn):
    (size,) = struct.unpack("!I", recv_exact(conn, 4))
    if size > MAX_FRAME:
        raise ValueError("frame too large")
    return recv_exact(conn, size)
```

发送端也要用 `sendall()`，并约定编码、错误响应和协议版本。长度上限不是优化项，而是阻止异常客户端诱导服务分配无限内存的边界。

![UDS 消息分帧与内核缓冲区](/images/unix-domain-socket-local-ipc/framing.jpeg)

## 路径本身就是安全边界

Socket 文件应放在服务专用目录，而不是所有用户都可写的位置。用户级服务可使用 `$XDG_RUNTIME_DIR` 下权限为 `0700` 的子目录；系统服务通常由服务管理器创建受控的运行目录。`bind()` 后再把 Socket 文件权限收紧到 `0600` 或按共享组设置 `0660`。

不要在启动时无条件删除同名路径：它可能是普通文件，也可能属于仍在运行的实例。更稳妥的流程是先确保父目录可信，再用 `lstat()` 检查类型；若路径是 Socket，可先尝试连接判断是否已有服务监听，只在确认是残留端点后删除。私有目录能显著减少检查与删除之间被替换的竞态。

文件权限控制“谁能连接”，却不等于完整身份认证。Linux 服务若需要按调用者授权，可读取对端凭据，例如通过 `SO_PEERCRED` 获取进程的 UID、GID 和 PID，再根据 UID 或组做策略判断；跨平台程序则应为凭据接口准备适配层。

## 把生命周期收完整

正常退出时应先停止接收新连接，等待在途请求到达期限，再关闭监听描述符并删除 Socket 文件。进程被强制终止时，文件系统路径可能残留，所以启动逻辑必须能够识别并清理陈旧端点。若由 systemd 等服务管理器托管，还可以考虑 Socket 激活，把监听端点生命周期交给管理器。

连接建立后同样需要治理：设置读写超时，限制并发连接数、单帧大小和每个连接的待处理请求数。客户端读取过慢会让服务端写缓冲区最终填满；若处理线程一直阻塞在写操作上，本机 IPC 也会产生级联拥塞。应让超时、拒绝和断连成为可统计的结果。

## 排障先看四个层次

```bash
ss -xlpn                 # 查看监听中的 Unix Socket
lsof -U                  # 查看持有 Unix Socket 的进程
stat /run/myapp/api.sock # 检查类型、属主与权限
socat - UNIX-CONNECT:/run/myapp/api.sock
```

先确认路径是否存在且类型正确，再检查父目录的执行权限和 Socket 的读写权限；随后确认确有进程监听，最后才检查应用协议。能连接却立即断开，通常是分帧、版本或请求上限问题；报 `Connection refused` 且没有监听者时，则要排查残留 Socket 文件。

上线前至少验证五种情况：重复启动、进程崩溃后重启、客户端发送半帧、超大帧、客户端停止读取。一个可靠的 UDS 服务，不只是本机“连得上”，还应在路径冲突、恶意输入与慢客户端出现时，仍能给出明确、可恢复的结果。
