---
title: TLS 证书报错别急着重签：用 OpenSSL 拆开证书链与握手
date: 2026-07-23 03:06:24
tags:
  - TLS
  - OpenSSL
  - 网络诊断
  - 可靠性
categories:
  - 工程实践
---

![TLS 证书链与安全连接](/images/tls-certificate-chain-troubleshooting/cover.jpeg)

浏览器提示“连接不安全”时，重新签发证书并不一定能解决问题。错误可能来自 SNI、主机名、有效期、中间证书、客户端信任库，也可能是负载均衡器仍在提供旧证书。有效的排障方式，是保留握手证据，再把“连接、身份、信任、部署”逐层拆开。

<!-- more -->

## 先确认故障发生在哪一层

TLS 之前还有 DNS 和 TCP，TLS 之后才是 HTTP。不要把所有 HTTPS 失败都归因于证书：

| 现象 | 优先检查 |
| --- | --- |
| `Connection refused` 或超时 | 端口监听、防火墙、路由 |
| 握手阶段直接断开 | 协议版本、密码套件、SNI、双向 TLS |
| 主机名不匹配 | SAN、访问域名、虚拟主机配置 |
| 无法建立信任链 | 中间证书、根证书、客户端信任库 |
| 握手成功但返回 4xx/5xx | HTTP 路由、鉴权、上游服务 |

先记录故障时间、客户端环境、目标域名和出口网络。不要急着重启代理或替换证书，否则现场可能随缓存和节点切换而消失。

## 用 `s_client` 保存完整握手

下面的命令同时发送 SNI、展示服务端提供的证书，并在验证失败时返回错误：

```bash
openssl s_client \
  -connect api.example.com:443 \
  -servername api.example.com \
  -showcerts \
  -verify_hostname api.example.com \
  -verify_return_error </dev/null
```

`-servername` 很关键：同一 IP 常承载多个域名，省略它可能拿到默认站点证书。`-verify_hostname` 才会明确检查访问域名是否出现在 SAN 中。只运行默认的 `s_client` 时，即使输出验证错误，程序也可能继续握手，因此不能只看退出状态。

若使用私有 CA，应通过 `-CAfile company-root.pem` 指定预期的信任锚，而不是用关闭验证来“修复”。记录服务端返回的证书顺序、`Verify return code`、协商协议和所有错误，之后才能稳定复现。

![客户端沿证书链建立信任](/images/tls-certificate-chain-troubleshooting/chain.jpeg)

## 分清叶子、中间与根证书

服务端通常应发送叶子证书和构建路径所需的中间证书；根证书应由客户端预先信任，一般不需要随握手发送。

| 角色 | 负责什么 | 排障重点 |
| --- | --- | --- |
| 叶子证书 | 证明当前服务身份 | SAN、有效期、用途 |
| 中间 CA | 把叶子连接到信任锚 | 是否遗漏、顺序与签名 |
| 根 CA | 客户端信任起点 | 是否存在于目标信任库 |

先查看线上实际提供的叶子证书，而不是只检查服务器磁盘上的文件：

```bash
openssl s_client -connect api.example.com:443 \
  -servername api.example.com </dev/null 2>/dev/null |
  openssl x509 -noout -subject -issuer -serial -dates \
    -fingerprint -sha256 -ext subjectAltName
```

有独立证书文件时，可离线验证路径：

```bash
openssl verify -CAfile root.pem \
  -untrusted intermediate.pem leaf.pem
```

“浏览器正常、脚本失败”不代表服务端配置正确。浏览器可能缓存过中间证书，不同运行时的信任库也可能不同；服务端仍应提供构链所需的中间证书，并在真实客户端环境中验证。

## 排查部署层的四个陷阱

第一，直接连接 IP 会改变 SNI 与主机名语义。要绕过 DNS 测试某个节点，使用：

```bash
curl -v --resolve api.example.com:443:203.0.113.10 \
  https://api.example.com/health
```

第二，确认客户端与服务端时钟；尚未生效和已经过期可能只是时间漂移。第三，逐个检查 CDN、入口网关和负载均衡节点，证书往往终止在这些位置，而不是应用进程。若故障只在部分请求出现，应比较各节点证书的 SHA-256 指纹。

第四，部署后确认私钥与叶子证书确实配对。下面两个摘要应一致，命令不会输出私钥内容：

```bash
openssl pkey -in server.key -pubout -outform PEM | openssl sha256
openssl x509 -in leaf.pem -pubkey -noout | openssl sha256
```

![从入口到后端逐层定位 TLS 故障](/images/tls-certificate-chain-troubleshooting/diagnosis.jpeg)

## 把证书检查放到故障之前

本地文件可以用 `-checkend` 判断是否会在指定秒数内过期，命令的退出码适合接入监控：

```bash
# 30 天内过期则返回非零
openssl x509 -in leaf.pem -checkend 2592000 -noout
```

监控还应从外部网络定期检查 SAN、完整证书链和实际指纹，而不只是读取证书管理平台的到期日。发布流程则要验证每个终止 TLS 的节点，并保留旧证书指纹用于回滚核对。

证书故障最怕把“重新签发”当作通用答案。先固定域名、地址、SNI、信任库和时间，再比较线上握手与预期文件，才能判断问题究竟在身份、构链、客户端，还是某个尚未更新的入口节点。
