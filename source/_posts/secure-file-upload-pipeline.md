---
title: 把文件上传入口守住：限额、识别、隔离与安全发布
date: 2026-07-24 03:07:35
tags:
  - 文件上传
  - Web 安全
  - Python
  - 后端
categories:
  - 工程实践
---

![安全文件上传管线](/images/secure-file-upload-pipeline/cover.jpeg)

头像、附件和数据导入看起来只是“接收一个文件”，实际上却把磁盘、解析器和下载域名同时暴露给了不可信输入。只检查扩展名或请求头，挡不住伪装类型、超大文件、路径穿越和带主动内容的文档。稳妥的设计不是再补一个判断，而是建立一条先隔离、后验证、最后发布的管线。

<!-- more -->

## 先定义信任边界

客户端提交的 `filename`、`Content-Type`、`Content-Length` 都只能作为提示，不能作为安全结论。文件名可能包含 `../`，类型声明可以随意填写，长度也可能缺失或与实际流量不同。服务端应为每个对象生成随机存储键，把原始文件名仅作为经过长度限制和字符清理的展示元数据。

上传入口至少要同时限制单文件大小、请求总大小、文件数量和用户配额。网关限制可以尽早拒绝明显超限的请求，应用层仍需按实际读取的字节计数，避免分块传输或配置差异绕过保护。

```python
from pathlib import Path
from secrets import token_hex

QUARANTINE = Path("/srv/uploads/quarantine")
MAX_BYTES = 10 * 1024 * 1024

def save_limited(stream):
    key = token_hex(16)          # 不使用用户提供的文件名
    target = QUARANTINE / key
    total = 0
    try:
        with target.open("xb") as output:
            while chunk := stream.read(64 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("file too large")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return key, total
```

这段代码用 `xb` 防止覆盖已有对象，并在失败时删除半成品。生产环境还应让隔离目录不可执行、不可被 Web 服务器直接访问，并为并发上传设置总量和速率上限。

## 类型校验要看内容，也要能解析

扩展名和 MIME 请求头适合改善交互，却不适合决定是否放行。第一层应根据文件签名字节识别候选格式，第二层再用对应解析器完整读取；两者不一致就拒绝。单看“魔数”仍不够，因为一个文件可以拥有合法开头，却在后续结构中藏着畸形数据。

| 风险 | 不可靠做法 | 更稳的控制 |
| --- | --- | --- |
| 伪装类型 | 只看 `.jpg` | 签名识别后完整解码 |
| 内存耗尽 | 一次读入全部内容 | 流式计数并限制并发 |
| 路径穿越 | 拼接原始文件名 | 随机键与固定根目录 |
| 主动内容 | 上传成功即公开 | 隔离扫描后再发布 |
| 解压炸弹 | 自动展开压缩包 | 默认不解压或设置展开上限 |

图片可以限制像素总数，成功解码后重新编码为允许的格式，且不要复制原始元数据。PDF、Office 文档和压缩包的解析面更复杂，应交给权限受限、可超时、可销毁的工作进程；不要让 Web 主进程直接打开它们。

![文件从隔离区经过多重检查后进入存储](/images/secure-file-upload-pipeline/validation-pipeline.jpeg)

## 用状态机隔开“已接收”和“可下载”

上传接口返回成功，只代表字节已安全落入隔离区，不代表文件可以公开。元数据可以使用 `pending → scanning → ready` 状态流转，失败则进入 `rejected`；只有 `ready` 对象才能生成下载地址。扫描超时或服务异常应保持不可访问，不能因为检查失败而默认放行。

异步任务要记录对象摘要、识别出的真实类型、扫描器结果和规则版本。相同任务重复执行时，根据对象键与摘要更新同一条记录，避免重试创建多份公开副本。隔离对象还应配置过期清理，防止失败任务长期占满存储。

## 下载端仍然需要防线

发布后的文件最好由独立下载域名或对象存储提供，该域名不携带业务 Cookie。对普通附件设置 `Content-Disposition: attachment`，并返回准确的 `Content-Type` 与 `X-Content-Type-Options: nosniff`。确需内联展示的图片也应采用允许列表，不要让用户上传的 HTML、SVG 或脚本与主站共享同源权限。

最后，把拒绝原因、大小分布、扫描耗时、超限次数和积压量做成指标。一次可靠的上传应满足四个条件：资源消耗有上限，文件身份可验证，检查期间不可访问，发布与下载权限彼此隔离。这样即使解析器出现缺陷，影响也会被限制在可观测、可清理的边界内。
