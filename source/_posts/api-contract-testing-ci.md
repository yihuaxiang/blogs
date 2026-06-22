---
title: 用 API 契约测试守住前后端协作边界
date: 2026-06-23 03:03:35
tags:
  - API
  - 测试
  - CI
  - 工程实践
categories:
  - 工程实践
---

![用 API 契约测试守住前后端协作边界](/images/api-contract-testing-ci/cover.jpeg)

前后端联调最怕的不是接口还没写完，而是大家以为已经说清楚了：字段名临时改了、枚举多了一个值、错误结构不一致、分页参数含义不同。API 契约测试的价值，就是把这些口头约定变成可执行的检查，让接口变更在进入主干和发布前就暴露出来。

<!-- more -->

## 为什么只靠联调不够

传统联调通常发生在功能接近完成时。这个时间点发现问题，往往意味着前端要改适配、后端要补兼容、测试要重新走流程，成本已经被放大。

| 风险 | 常见表现 | 契约化做法 |
| --- | --- | --- |
| 字段漂移 | `userId` 改成 `id` | 在响应 schema 中固定字段名 |
| 类型漂移 | 金额从数字变成字符串 | 用类型校验阻断不兼容变更 |
| 错误不一致 | 有时返回 `message`，有时返回 `msg` | 统一错误模型 |
| 隐式必填 | 文档没写，但服务端实际要求 | 把 required 写进契约 |

契约测试不是替代端到端测试，而是把“接口形状是否一致”这件事前移。它检查的是边界：请求能不能被服务端理解，响应能不能被调用方稳定解析，错误能不能被 UI 或上游服务可靠处理。

## 契约先写清楚接口形状

![API 契约地图](/images/api-contract-testing-ci/contract-map.jpeg)

一个实用契约不需要一开始覆盖所有细节，但至少要包含路径、方法、参数、状态码、请求体、响应体和错误结构。OpenAPI 很适合作为团队共享格式，因为它既能被人阅读，也能被工具消费。

```yaml
paths:
  /orders/{orderId}:
    get:
      parameters:
        - name: orderId
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: order detail
          content:
            application/json:
              schema:
                type: object
                required: [id, status, amount]
                properties:
                  id:
                    type: string
                  status:
                    type: string
                    enum: [created, paid, canceled]
                  amount:
                    type: integer
                    minimum: 0
        "404":
          description: order not found
```

这里的重点不是文档写得漂亮，而是字段约束足够明确。比如金额用分为单位的整数，状态只能来自有限枚举，`id`、`status`、`amount` 必须存在。规则进入契约后，调用方就可以生成类型、Mock 数据和校验用例。

## Mock 让开发提前并行

契约写好后，前端不必等后端完整实现。可以用契约启动 Mock 服务，先把页面状态、异常分支和数据空态做出来。后端也可以用同一份契约生成校验用例，确保真实实现没有偏离文档。

一个推荐流程是：

1. 产品需求确认后，先提交接口契约草案。
2. 前后端一起评审字段、状态码和错误模型。
3. 前端基于契约 Mock 开发，后端基于契约实现。
4. CI 中分别校验契约格式、后端响应和前端消费。

这样做的好处是，争议会集中在契约评审阶段。契约一旦合并，谁修改接口形状，谁就需要同步更新契约并说明兼容策略。

## CI 中阻断破坏性变更

![CI 契约闸门](/images/api-contract-testing-ci/ci-gate.jpeg)

契约测试真正发挥作用，必须进入 CI。只在本地手工跑一次，很快就会被赶进度时跳过。CI 可以分成三类检查：

| 检查 | 目标 | 失败时说明 |
| --- | --- | --- |
| 格式检查 | 契约文件能被解析 | YAML、引用或 schema 写错 |
| 实现检查 | 真实接口符合契约 | 后端返回字段、类型或状态码不符 |
| 兼容检查 | 新契约没有破坏旧调用方 | 删除字段、收窄枚举、改变类型 |

示例脚本可以保持简单，先把动作固定下来，再替换成团队实际使用的工具：

```json
{
  "scripts": {
    "contract:lint": "openapi lint openapi.yaml",
    "contract:test": "contract-test --schema openapi.yaml --base-url http://localhost:3000",
    "contract:diff": "contract-diff main:openapi.yaml openapi.yaml"
  }
}
```

CI 流水线里可以把契约检查放在单元测试之后、部署之前：

```yaml
steps:
  - run: npm ci
  - run: npm test
  - run: npm run contract:lint
  - run: npm run contract:test
  - run: npm run contract:diff
```

`contract:diff` 尤其重要。新增可选字段通常是兼容的；删除字段、把可选改必填、把字符串改数字、收窄枚举值，通常都应该被视为破坏性变更。破坏性变更不是绝对不能做，但必须有版本切换、灰度期或调用方同步计划。

## 错误模型也要纳入契约

很多团队只校验成功响应，结果真正线上出问题时，前端拿到的错误结构无法识别。建议把错误响应也标准化，例如统一包含 `code`、`message`、`request_id` 和可选的 `details`。

```json
{
  "code": "ORDER_NOT_FOUND",
  "message": "order not found",
  "request_id": "req_7f2c",
  "details": {
    "order_id": "o_123"
  }
}
```

错误码要稳定，`message` 可以给人读，但业务分支最好依赖 `code`。如果前端需要针对库存不足、权限失败、支付超时展示不同状态，这些错误码就应该出现在契约里，而不是藏在后端代码或聊天记录中。

## 落地清单

小团队可以从一条关键接口开始，不必一次性改造全部系统。先选登录、下单、支付回调、账单导出这类高价值边界，把契约文件、Mock、后端校验和 CI 阻断跑通。

一套可维护的 API 契约机制，至少应该满足这几条：

1. 契约文件和业务代码同仓库或同变更流程管理。
2. 字段名、类型、必填项、枚举、错误结构都写进 schema。
3. Mock、类型生成、服务端校验尽量复用同一份契约。
4. CI 对格式错误和破坏性变更直接失败。
5. 对确实需要破坏兼容的接口，提前设计版本、灰度和迁移窗口。

契约测试的核心不是增加流程，而是减少猜测。当前后端都围绕同一份可执行契约协作，接口变更就不再依赖记忆和口头同步，质量风险也会更早、更便宜地暴露出来。
