---
title: DNS 故障别靠刷新：用 dig 分层定位解析链路
date: 2026-07-18 03:08:29
tags:
  - DNS
  - Linux
  - 网络诊断
  - 可靠性
categories:
  - 工程实践
---

![DNS 分层解析与故障定位](/images/dns-resolution-troubleshooting/cover.jpeg)

域名打不开时，反复刷新、清浏览器缓存或直接改 `hosts`，往往只会暂时绕过问题。DNS 不是一次简单查询，而是一条包含本机、递归解析器、根与顶级域、权威服务器的缓存链路。排障的关键，是先确认“谁给出了什么答案”，再逐层缩小故障范围。

<!-- more -->

## 先分清解析链路

应用通常把域名交给系统的存根解析器，后者依据 `/etc/resolv.conf` 请求递归解析器。缓存未命中时，递归解析器再沿根域、顶级域和权威服务器寻找记录，并按 TTL 缓存结果。浏览器、运行时和系统服务还可能拥有各自的缓存，因此“同一台机器”也未必只有一个答案来源。

| 现象 | 优先检查 |
| --- | --- |
| 只有一个进程失败 | 应用内缓存、代理或运行时配置 |
| 同一网络内都失败 | 企业递归解析器、转发器或防火墙 |
| 部分地区失败 | 权威节点、委派、缓存尚未收敛 |
| 域名有答案但连接失败 | 路由、端口、TLS，而非继续查 DNS |

## 用最小查询保留证据

先不要清缓存。运行普通查询并记录状态、应答、TTL、响应服务器与耗时：

```bash
dig api.example.com A
dig api.example.com AAAA
dig api.example.com CNAME

# 只保留关键区段，便于粘贴到故障记录
dig api.example.com A +noall +answer +authority +comments
```

`status: NOERROR` 只表示查询被正常处理，不保证 `ANSWER` 中有目标记录；空应答可能意味着该名称存在，但请求的记录类型不存在。`NXDOMAIN` 表示名称不存在，`SERVFAIL` 常见于上游超时、DNSSEC 校验或权威侧异常，`REFUSED` 则说明服务器拒绝回答。还要看 `SERVER` 字段，避免以为查了公司 DNS，实际却被 VPN 或容器改到了另一台解析器。

![用 dig 沿 DNS 链路逐层定位故障](/images/dns-resolution-troubleshooting/dns-path.jpeg)

## 对比递归、追踪与权威答案

分别询问默认解析器和指定解析器，可以判断异常是否局限在某个缓存或网络环境：

```bash
dig api.example.com A
dig @10.0.0.53 api.example.com A

# 从根委派开始追踪；受限网络可能禁止直接访问外部 DNS
dig +trace api.example.com A
```

`+trace` 展示委派路径，但它绕过日常使用的递归缓存，不能代替对实际解析器的检查。若追踪停在某一级，重点查看上一层返回的 `NS` 与 glue 记录；若能到达权威服务器，则直接查询并禁止递归：

```bash
dig example.com NS +short
dig @ns1.example.net api.example.com A +norecurse
dig @ns2.example.net api.example.com A +norecurse
```

多个权威服务器应返回一致的记录与序列状态。若只有一台给出旧值，问题通常不在客户端，而在区域同步或发布流程。

## 正确认识 TTL 与负缓存

TTL 是缓存答案的剩余生存时间，不是“变更后保证在此刻全球生效”的倒计时。不同解析器可能在不同时间缓存旧值；删除记录产生的否定答案也会依据 SOA 参数被缓存。排查不存在的名称时，应同时查看权威区段：

```bash
dig missing.example.com A +noall +comments +authority
dig example.com SOA +noall +answer
```

计划切换地址时，应提前降低 TTL，等待旧缓存自然过期后再变更，并在稳定后恢复合理值。故障中直接把 TTL 改成极小值，无法驱逐已经缓存的旧答案，还会增加权威服务器压力。

## 把排障变成可复现流程

一次完整记录至少包含查询时间、客户端网络、查询名称与类型、使用的解析器、完整 `dig` 输出，以及所有权威服务器的对比结果。确认 DNS 已返回预期地址后，再用 `curl --resolve` 把“解析”和“连接”拆开验证：

```bash
curl -v --resolve api.example.com:443:203.0.113.10 \
  https://api.example.com/health
```

这条命令保留域名对应的 Host 与 TLS 语义，只把连接固定到测试地址。若它成功而普通访问失败，应继续追查解析；若它也失败，则转向服务监听、证书、负载均衡和网络路径。

DNS 排障最忌讳一上来就清缓存，因为现场证据会随之消失。先确认答案来自哪里，再比较递归与权威结果，最后才处理缓存或网络。把每一层的输入与输出留下来，模糊的“域名偶尔不通”就能变成可定位、可复现的问题。
