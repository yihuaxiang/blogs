---
title: 别把 SameSite 当万能药：CSRF 防护的令牌、来源校验与上线清单
date: 2026-09-11 03:02:44
tags:
  - Web 安全
  - CSRF
  - Cookie
  - 后端
categories:
  - 工程实践
---

![浏览器请求穿过多层 CSRF 防线](/images/csrf-defense-samesite-origin-token/cover.jpeg)

用户已经登录的站点若用 Cookie 保存身份，浏览器会在满足条件时自动带上它。攻击者不必偷到 Cookie，只要诱导用户在另一个站点提交表单，就可能让目标站点误以为这是用户本人的转账、改邮箱或解绑操作。这就是跨站请求伪造（CSRF）。可靠的防护不是单押某个响应头，而是先缩小 Cookie 的跨站发送范围，再让服务端验证请求确实来自自己的页面。

<!-- more -->

## 先划清 CSRF 的适用边界

CSRF 针对的是“浏览器替用户自动附带凭证”的状态变更请求，常见于 Session Cookie、登录 Cookie 和客户端证书。攻击者通常能发起表单提交，却读不到响应内容；因此它与 XSS、接口越权不是同一类问题。若接口只接受放在 `Authorization` 头中的短期令牌，普通跨站表单不能自行附上该头，CSRF 风险会显著降低，但 XSS 窃取令牌和接口鉴权仍要分别处理。

| 请求形态 | 主要风险 | 基线做法 |
| --- | --- | --- |
| `GET`、`HEAD` | 被预取、链接或图片触发 | 必须无副作用，不能用来删除或确认 |
| Cookie 身份的 `POST` / `PATCH` / `DELETE` | 跨站自动携带登录态 | 来源校验 + CSRF Token |
| Bearer Token API | 令牌泄露、越权 | 严格鉴权、CORS 与 XSS 防护 |

第一条规则最重要：只读操作才使用安全方法。把“退出登录”或“修改订阅”设计成 `GET`，会让任何后续防线都变得被动。

## SameSite 是减伤层，不是通行证

![受保护的表单请求携带 Cookie 与令牌](/images/csrf-defense-samesite-origin-token/request-flow.jpeg)

会话 Cookie 至少应设置 `Secure`、`HttpOnly` 与合适的 `SameSite` 属性。`Secure` 防止明文 HTTP 传输，`HttpOnly` 减少脚本直接读取 Cookie 的机会；`SameSite=Lax` 会阻止多数跨站子请求携带 Cookie，但顶级导航的某些安全方法仍可携带。`SameSite=Strict` 更保守，却可能影响从外部链接进入站点后的体验。只有明确需要第三方上下文时才选择 `None`，并且它必须配合 `Secure`。

不要把 `SameSite` 当成服务端授权结论：旧客户端、兼容策略、同站不同子域以及未来业务改动都可能改变边界。尤其“same-site”并不等于“same-origin”；若不受控的子域也在同一站点下，它未必值得信任。敏感写操作仍应由服务端检查来源和令牌。

## 为每次写操作验证两个证据

来源校验回答“请求从哪个页面来”，Token 回答“页面是否先从本站取得了不可预测的值”。对浏览器页面提交的写请求，优先严格比对 `Origin` 与预期源；部分场景缺少 `Origin` 时，可谨慎退回到解析后的 `Referer`，缺失或格式异常则拒绝。`Sec-Fetch-Site` 可作为额外信号：跨站写请求直接拒绝，但不要把它当作唯一依据。

Token 可以保存在服务端 Session 中，渲染进表单隐藏字段，或由前端从受保护页面读出后放进自定义请求头。下面是 Session Token 的简化示例，比较时使用常量时间函数，避免把差异留给旁路观察：

```js
import crypto from "node:crypto";

const expectedOrigin = "https://app.example.com";

function rejectCrossSiteWrite(req, res, next) {
  if (!["POST", "PUT", "PATCH", "DELETE"].includes(req.method)) return next();

  const origin = req.get("Origin");
  const supplied = Buffer.from(req.body.csrfToken || "");
  const expected = Buffer.from(req.session.csrfToken || "");
  const tokenMatches = supplied.length === expected.length &&
    crypto.timingSafeEqual(supplied, expected);

  if (origin !== expectedOrigin || !tokenMatches) {
    return res.status(403).json({ error: "invalid request origin" });
  }
  next();
}
```

Token 要足够随机、绑定用户会话，并在登录、登出、权限提升后轮换。不要把它写入 URL、访问日志或第三方跳转参数。对纯 HTML 表单放隐藏字段即可；对 `fetch` 请求，使用自定义头能让跨站表单更难伪造，但服务器仍应保留来源和 Token 校验。

## 把防线做成可验证的组合

![Cookie 策略、来源校验和令牌组成纵深防线](/images/csrf-defense-samesite-origin-token/defense-layers.jpeg)

推荐的处理顺序是：路由先区分读写方法 → 对写操作检查 `Origin`/`Referer` 与 Fetch Metadata → 验证 CSRF Token → 再进入业务鉴权和事务。CORS 不应承担这里的职责：它主要限制脚本**读取**跨源响应，不能阻止浏览器发出表单或简单请求。

| 控制项 | 拦住什么 | 常见遗漏 |
| --- | --- | --- |
| 安全的 HTTP 方法语义 | 意外触发的状态变更 | 用 `GET` 执行写操作 |
| Cookie 的 `SameSite` | 大部分跨站 Cookie 携带 | 忽略同站子域边界 |
| Origin / Referer | 非本站页面发起的写请求 | 只做字符串前缀匹配 |
| Session CSRF Token | 缺少本站页面上下文的伪造 | Token 固定、可预测或进 URL |
| 重新认证或二次确认 | 高价值、不可逆操作 | 所有动作只依赖登录态 |

上线前应写出四类自动化测试：本站表单带正确 Token 成功；外站表单没有 Token 返回 403；Token 错误或会话轮换后返回 403；带 Cookie 的跨站写请求同样失败。监控中只记录路由、失败类别、请求 ID 和经过脱敏的来源，不记录 Token、Cookie 或完整 Referer 查询串。若某个第三方回跳确需跨站状态变更，把它收敛为一次性、短时有效且可审计的流程，而不是为整站放宽 Cookie 策略。

CSRF 防护的核心不是给每个接口机械加一个字段，而是承认浏览器会替用户携带身份，并要求每一次写操作额外证明“它来自我信任的页面”。当方法语义、Cookie 属性、来源校验和随机 Token 共同生效时，攻击者即使能诱导点击，也难以把用户的登录态变成自己的操作能力。
