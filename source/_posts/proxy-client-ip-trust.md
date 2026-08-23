---
title: 反向代理后的客户端 IP 别直接信：建立可信代理边界
date: 2026-08-24 03:01:13
tags:
  - HTTP
  - 反向代理
  - Web 安全
categories:
  - 后端工程
---

![反向代理与客户端 IP 信任链](/images/proxy-client-ip-trust/cover.jpeg)

限流、审计和地域判断经常依赖客户端 IP，但应用部署到 CDN、负载均衡器或网关之后，套接字看到的通常只是上一跳代理。直接取 `X-Forwarded-For` 的最左值看似解决了问题，却可能把攻击者自己填写的地址当成事实。真正可靠的做法不是“找一个请求头”，而是先划出可信代理边界。

<!-- more -->

## 三种地址不是一回事

假设请求依次经过边缘网关和内部代理：

```text
client -> edge proxy -> internal proxy -> application
```

应用拿到的地址大致分为三类：

| 来源 | 表示什么 | 能否直接用于安全决策 |
| --- | --- | --- |
| TCP peer address | 与应用直接建连的一跳 | 能确认连接来源，但通常是代理 |
| `X-Forwarded-For` | 多跳代理追加的地址链 | 只有可信代理写入的部分可用 |
| `Forwarded: for=` | 标准化的转发信息 | 语法更完整，信任问题仍然存在 |

请求头由客户端发送，因此下面的值完全可以伪造：

```http
X-Forwarded-For: 127.0.0.1
```

如果边缘代理只是追加真实来源，最终地址链可能变成 `127.0.0.1, 198.51.100.27`。应用若无条件选择最左侧，就会绕过按 IP 设置的后台限制或限流规则。

## 从右向左找到第一个不可信地址

![从应用侧逐跳检查代理链](/images/proxy-client-ip-trust/trust-chain.jpeg)

信任判断应从应用实际连接的 peer 开始，而不是从用户声称的起点开始：

1. peer 不在可信代理网段内时，忽略全部转发头，直接使用 peer。
2. peer 可信时，将地址链从右向左检查。
3. 跳过明确配置为可信的代理，遇到第一个不可信地址就停止；它才是当前边界下可确认的客户端。
4. 地址缺失、格式非法或链条过长时，拒绝请求或标记为未知，不要猜测。

下面的 Python 代码只用于展示核心算法。生产环境应优先使用框架经过测试的代理解析组件，并正确处理 IPv6、引号和多个同名请求头：

```python
from ipaddress import ip_address, ip_network

TRUSTED = [
    ip_network("10.20.0.0/24"),  # internal proxy
    ip_network("10.30.0.0/24"),  # edge proxy
]

def is_trusted(addr):
    return any(addr in network for network in TRUSTED)

def resolve_client_ip(peer, x_forwarded_for):
    peer_addr = ip_address(peer)
    if not is_trusted(peer_addr):
        return str(peer_addr)

    if not x_forwarded_for:
        return None

    try:
        chain = [
            ip_address(item.strip())
            for item in x_forwarded_for.split(",")
            if item.strip()
        ]
    except ValueError:
        return None

    for addr in reversed(chain):
        if not is_trusted(addr):
            return str(addr)
    return str(chain[0]) if chain else None
```

这里的关键不是 CIDR 写法，而是可信列表必须对应真实拓扑。为了省事而信任整个私网、任意跳数或所有来源，相当于重新打开伪造入口。

## 在入口处收口规则

最简单的治理方式，是让唯一的公网入口删除客户端传入的 `Forwarded`、`X-Forwarded-For`、`X-Real-IP`，再以统一格式重新生成。内部代理只能追加，应用只信任预先登记的代理地址。若使用 Nginx 的 realip 模块，可以表达为：

```nginx
set_real_ip_from 10.20.0.0/24;
set_real_ip_from 10.30.0.0/24;
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```

代理实例地址若会弹性变化，应从基础设施配置生成白名单，而不是临时放宽网段。同时保留原始 peer、解析后的客户端地址和经过的代理链，便于审计算法是否符合实际流量。

## 验证不能只测正常请求

至少准备四组自动化用例：直连应用时伪造请求头、经单层可信代理、经多层可信代理，以及包含非法 IPv6 或超长地址链的请求。然后确认限流、访问日志和鉴权读取的是同一个“已解析地址”字段，而不是各自再解析一遍。

客户端 IP 不是某个头部里的天然真相，而是网络拓扑和信任配置共同推导出的结果。把解析集中在入口、从右向左跨越可信代理，并对异常输入失败关闭，才能让它真正承担安全与审计职责。
