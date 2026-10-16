---
title: TCP 半关闭不是断线：FIN、RST 与应用层优雅收尾
date: 2026-10-17 03:03:29
tags:
  - TCP
  - 网络编程
  - Linux
  - 稳定性
categories:
  - 工程实践
---

![TCP 双向连接的优雅收尾](/images/tcp-half-close-graceful-finish/cover.jpeg)

调用方发完请求后，既想告诉服务端“我不会再发送”，又要继续接收剩余响应。直接关闭 Socket 会把两个方向一起交给内核处理，无法清楚表达这种意图。TCP 半关闭提供了更精确的语义：关闭一侧的发送方向，同时保留接收方向，让协议能够有序收尾。

<!-- more -->

## TCP 是两条独立的字节流

一条 TCP 连接是全双工的。A 到 B 和 B 到 A 各有独立的发送序列，因此某一方发出 `FIN`，只表示“这个方向不会再有新字节”，并不禁止对端继续发送。

| 现象 | 本地读取结果 | 通常含义 |
| --- | --- | --- |
| 对端发送 `FIN` | `recv()` 返回空字节串 | 对端已正常结束发送 |
| 暂时没有数据 | 阻塞、超时或返回 `EAGAIN` | 字节流仍可能继续 |
| 收到 `RST` | 读取或写入报连接重置 | 连接被异常终止 |

这里最容易误判的是 EOF。对流式 Socket 来说，`recv()` 返回 `b""` 才代表对端发送方向结束；一次短读只表示当前拿到了这些数据，不能当作消息结束。应用协议仍需使用长度头、分隔符或明确的关闭语义划分消息边界。

![TCP 半关闭后的双向状态变化](/images/tcp-half-close-graceful-finish/half-close-flow.jpeg)

## 用 shutdown 表达“我发完了”

`close()` 释放整个 Socket，而 `shutdown()` 可以只改变一个方向。Python 中的 `shutdown(socket.SHUT_WR)` 会停止后续发送，并促使已排队数据发送完后向对端发出 `FIN`；调用方仍可继续读取响应。

下面用 `socketpair()` 在本机演示这一语义：

```python
import socket
import threading

client, server = socket.socketpair()

def serve():
    request = bytearray()
    while chunk := server.recv(4096):
        request.extend(chunk)
    server.sendall(b"received:" + bytes(request))
    server.close()

threading.Thread(target=serve).start()
client.sendall(b"hello")
client.shutdown(socket.SHUT_WR)  # 请求发送完毕，但仍可读响应

response = bytearray()
while chunk := client.recv(4096):
    response.extend(chunk)
print(response.decode())
client.close()
```

服务端读到 EOF 后才开始处理，适合“请求体持续到连接半关闭”的简单协议。HTTP/1.1 等复用连接不能用这种方式标记每个请求结束，因为半关闭之后该方向无法恢复；它们必须依赖长度或分块编码。

## FIN 与 RST 不要混为一谈

正常关闭追求的是：先发送完应用数据，再用 `FIN` 结束方向，最终让双方都确认关闭。`RST` 则表示连接状态无法继续，未读取的数据、进程异常退出或不恰当的 Socket 选项都可能使对端看到 `Connection reset by peer`。

因此，收到 EOF 可以处理已经完整读到的消息；收到重置时却要谨慎判断响应是否完整。客户端不能因为“已经读到一部分 JSON”就当作成功，服务端也不能在写入用户态缓冲区后立刻认定对端已收到。真正需要交付确认的业务，应在应用层增加响应、请求 ID 与幂等处理，而不是把 TCP 的正常关闭当成业务确认。

## 优雅收尾仍然需要期限

半关闭不是无限等待。若对端始终不结束响应，调用方会永久占用文件描述符和连接槽位。可靠实现应分别设置读写期限，并限制响应大小；超时后记录阶段，再主动终止连接。

排障时可用下面的命令观察状态与系统调用：

```bash
ss -tanp | grep -E 'FIN-WAIT|CLOSE-WAIT|LAST-ACK'
strace -f -e trace=network,close ./your-client
tcpdump -nn -i any 'tcp[tcpflags] & (tcp-fin|tcp-rst) != 0'
```

大量 `CLOSE_WAIT` 通常说明进程已经收到对端 `FIN`，却迟迟没有关闭自己的 Socket；大量 `FIN_WAIT2` 则要检查对端是否从不结束另一方向。抓包用于确认线上发生了 `FIN` 还是 `RST`，`strace` 用于回答应用是否调用了 `shutdown()`、`close()`，两类证据要结合看。

设计连接收尾时，先明确协议如何判断一条消息完整，再决定是否需要半关闭。把 EOF、超时和重置作为三种不同结果记录，给排空过程设置上限，并让业务成功拥有独立确认，才能既不截断响应，也不把连接永远留在半关闭状态。
