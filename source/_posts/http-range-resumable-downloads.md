---
title: 断点下载不只是加个 Range：HTTP 字节范围、校验器与文件拼接
date: 2026-09-03 03:04:11
tags:
  - HTTP
  - 文件传输
  - 可靠性
  - 故障排查
categories:
  - 工程实践
---

![HTTP 断点下载的数据分片](/images/http-range-resumable-downloads/cover.jpeg)

大文件下载到 90% 时网络中断，最浪费的做法是从头再来。HTTP 字节范围请求允许客户端只取缺失部分，但可靠的断点下载绝不是把新响应直接追加到旧文件：服务器可能忽略范围，资源也可能已经更新。只有同时验证状态码、范围边界和资源版本，拼接后的文件才可信。

<!-- more -->

## 先读懂一次范围交换

假设本地临时文件已有 1,048,576 字节，客户端可请求从下一字节到末尾：

```http
GET /releases/app.tar HTTP/1.1
Host: download.example.com
Range: bytes=1048576-
Accept-Encoding: identity
```

服务器接受范围时应返回 `206 Partial Content`，并用 `Content-Range` 声明本段起止位置和完整长度：

```http
HTTP/1.1 206 Partial Content
Content-Range: bytes 1048576-8388607/8388608
Content-Length: 7340032
ETag: "release-v7"
```

`Accept-Ranges: bytes` 只是服务器支持范围请求的提示；没有该响应头，也可以实际发起请求验证。客户端真正该依据的是本次响应：

| 响应 | 含义 | 正确处理 |
| --- | --- | --- |
| `206` | 返回了指定范围 | 校验起点后追加 |
| `200` | 忽略范围并返回完整资源 | 覆盖临时文件，绝不能追加 |
| `416` | 请求范围无法满足 | 核对完整长度，判断已完成或重新下载 |

![范围请求的响应分支](/images/http-range-resumable-downloads/range-flow.jpeg)

即使收到 `206`，也要解析 `Content-Range`，确认起点恰好等于本地长度。只检查 `Content-Length` 不够，因为它描述的是本次响应体，而非完整文件。若下载链路会透明压缩，字节偏移还可能对应压缩后的表示；续传前后应保持请求头一致，下载二进制文件时可显式使用 `Accept-Encoding: identity`。

## 用 If-Range 防止拼错版本

断线期间，服务器上的同名文件可能已经发布了新版本。此时旧文件前半段与新文件后半段长度都合法，简单追加却会产生无法使用的混合文件。

首次下载应把强 `ETag` 与 URL、完整长度一起写入元数据。续传时同时发送：

```http
Range: bytes=1048576-
If-Range: "release-v7"
```

若校验器仍匹配，服务器返回 `206`；若资源已改变，服务器返回完整的 `200`，客户端据此清空旧分片并重新保存。`Last-Modified` 也可用于 `If-Range`，但时间精度和来源可靠性通常不如强 `ETag`。弱 ETag 不能保证逐字节一致，不适合这里的拼接条件。

## 把下载器写成状态机

![断点文件与元数据的校验状态机](/images/http-range-resumable-downloads/validation-state.jpeg)

不要让“是否追加”散落在网络回调里。把决策集中成几个可测试的分支：

```text
local_size = size("app.tar.part")
response = GET(range=local_size, if_range=saved_etag)

if response.status == 206 and response.range_start == local_size:
    append_and_flush(response.body)
elif response.status == 200:
    replace_partial_file(response.body)
elif response.status == 416 and response.total == local_size:
    mark_complete()
else:
    discard_response_and_retry_safely()
```

临时文件可使用 `.part` 后缀，旁边保存一个 JSON 元数据文件，记录规范化 URL、ETag、预期总长度和已落盘长度。进程崩溃后，以实际文件大小为准修正元数据；写入成功并刷新到磁盘后，再推进断点。全部完成时校验服务端提供的 SHA-256，最后原子改名为正式文件。

## 并行分块并不总是更快

多段并发下载会增加连接、临时文件和合并逻辑，还可能触发服务端限流。只有单连接确实受限、文件足够大且服务器稳定支持范围时才值得使用。所有分段必须绑定同一个强校验器，并逐段验证 `Content-Range`；任一分段返回 `200`，都不能把响应体当作该段写入。

服务端同样要防御畸形或大量重叠范围，限制单次请求的范围数量，并让不支持范围的动态内容明确退回完整响应。客户端则应限制文件总长度、重定向次数和重试预算，避免错误响应无限消耗磁盘。

可靠断点下载的核心是一份可验证的协议：`Range` 指出缺口，`Content-Range` 证明响应落点，`If-Range` 保证前后属于同一版本，哈希确认最终内容。把这四层证据都接上，网络中断才只是一次可恢复事件，而不是悄悄损坏文件的起点。
