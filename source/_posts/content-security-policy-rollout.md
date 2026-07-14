---
title: 把 CSP 落稳：从 Report-Only 到 nonce 的渐进防护清单
date: 2026-07-15 03:06:41
tags:
  - Web 安全
  - CSP
  - XSS
  - 前端工程
categories:
  - 工程实践
---

![内容安全策略防护浏览器资源加载](/images/content-security-policy-rollout/cover.jpeg)

一次遗漏的输出转义、一个被污染的富文本字段，都可能让脚本进入页面。内容安全策略（Content Security Policy，CSP）能让浏览器只执行可信来源的资源，为 XSS 再加一道边界。但直接上线严格策略，往往先把统计脚本、字体和业务代码一起拦掉。更稳妥的办法是先观察、再收敛、最后阻断。

<!-- more -->

## 先理解 CSP 的边界

CSP 通过 HTTP 响应头声明页面允许加载什么。它是纵深防御，不会替代模板转义、输入校验和依赖治理。

| 能力 | 适合处理 | 不能替代 |
| --- | --- | --- |
| 来源限制 | 阻止未知域名的脚本、图片和连接 | 服务端鉴权 |
| nonce 或 hash | 精确放行可信内联脚本 | HTML 输出编码 |
| 违规报告 | 发现页面依赖与异常注入 | 攻击告警的最终结论 |
| `frame-ancestors` | 限制页面被其他站点嵌入 | 业务操作的二次确认 |

策略的目标也不是“指令越多越安全”，而是每一项都能解释、能测试，并且不会靠长期保留 `*`、`'unsafe-inline'` 来维持可用。

## 用 Report-Only 盘点真实依赖

第一阶段只报告、不拦截。下面的响应头适合作为起点；为便于阅读写成多行，实际发送时应合并为一个头字段：

```http
Content-Security-Policy-Report-Only:
  default-src 'self';
  script-src 'self';
  object-src 'none';
  base-uri 'self';
  frame-ancestors 'none';
  report-uri /csp-reports
```

不要看到报告就逐条添加域名。先按“页面、指令、被拦来源”聚合，区分业务资源、浏览器扩展注入、旧页面残留和真正异常。报告端点还要限制请求体大小与频率，并删除 URL 查询参数等敏感信息；CSP 违规只是一条线索，不等同于发生了攻击。

## 用每次响应唯一的 nonce 放行脚本

严格策略最常卡在内联脚本。给所有脚本放开 `'unsafe-inline'` 会显著削弱防护；更好的做法是由服务端为每次 HTML 响应生成不可预测的 nonce，同时写进响应头和可信的 `<script>` 标签。

```js
import crypto from "node:crypto";

app.use((req, res, next) => {
  const nonce = crypto.randomBytes(16).toString("base64");
  res.locals.cspNonce = nonce;
  res.setHeader("Content-Security-Policy", [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'`,
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
  ].join("; "));
  next();
});
```

模板只把 nonce 交给确定可信的脚本：

```html
<script nonce="{{ cspNonce }}">
  window.bootstrapApp();
</script>
```

nonce 必须随响应变化，不能做成环境变量或用户会话常量。`onclick` 这类内联事件处理器也不会因为页面存在 nonce 自动获准，应迁移到外部脚本中的 `addEventListener`。对于内容固定的小脚本，也可在 `script-src` 中配置内容 hash；脚本任何字节变化时都要同步更新 hash。

## 按资源类型收紧白名单

![CSP 从观察到阻断的渐进发布流程](/images/content-security-policy-rollout/rollout.jpeg)

不要把所有来源塞进 `default-src`。明确的资源指令更容易审计，也能避免为了加载一张图片而意外允许同域脚本。

| 指令 | 管理对象 | 收紧时重点检查 |
| --- | --- | --- |
| `script-src` | JavaScript | 内联脚本、第三方 SDK、动态加载器 |
| `connect-src` | fetch、XHR、WebSocket | API、埋点和实时连接 |
| `img-src` | 图片 | CDN、`data:` 图片和用户内容 |
| `style-src` | 样式表 | CSS CDN、内联 style 与组件库 |
| `object-src` | 插件资源 | 通常可直接设为 `'none'` |
| `base-uri` | `<base>` 标签 | 通常限制为 `'self'` 或 `'none'` |

第三方域名要精确到协议和主机，避免 `https:` 或宽泛通配符。若页面使用 CDN 缓存 HTML，还要处理“每次响应唯一 nonce”与缓存的冲突：可在边缘动态注入 nonce，或把稳定脚本外置并使用 hash，不能让所有用户共享缓存中的固定 nonce。

## 分阶段切换到阻断模式

上线可以拆成四步：先在主要页面启用 Report-Only，覆盖一个完整业务流量周期；再清理内联事件、失效域名和不必要的第三方资源；随后用 `Content-Security-Policy` 强制执行已稳定的基线，同时用 Report-Only 试跑下一版严格策略；最后按页面或流量灰度，观察前端错误率、核心转化和违规报告。

强制策略与只报告策略可以并行存在。若同时发送多条强制策略，浏览器会同时执行它们，效果更接近取交集，而不是把后一条当作前一条的回退方案。

## 把策略纳入自动化检查

至少为登录、支付、编辑器等关键页面检查响应头，并在真实浏览器中跑一遍核心交互：

```bash
curl -sI https://example.test/account \
  | rg -i '^content-security-policy:'
```

测试应断言策略不含意外的 `*`、`'unsafe-inline'` 或 `'unsafe-eval'`，还要验证脚本启动、接口请求、图片和字体加载正常。策略变更应像代码一样经过评审：新增来源要注明用途、负责人和清理条件。

CSP 的价值不在于复制一段“最严格”的响应头，而在于建立可持续的资源边界。先用报告看清依赖，再用 nonce 或 hash 消除模糊放行，最后灰度进入阻断模式，安全收益才能落地而不以线上白屏为代价。
