---
title: 把 E2E 测试写稳：Playwright 的选择器、等待与 CI 诊断清单
date: 2026-06-26 03:05:40
tags:
  - Playwright
  - E2E
  - 测试
  - CI
  - 工程实践
categories:
  - 工程实践
---

![把 E2E 测试写稳](/images/playwright-e2e-testing-checklist/cover.jpeg)

端到端测试最容易被团队嫌弃，因为它一旦不稳定，就会在 CI 里反复制造红灯：本地能过、线上偶发失败、截图看不出原因。问题通常不在 Playwright 本身，而在用例把页面动画、接口耗时、测试数据和外部状态都当成了“刚好会按时出现”。要让 E2E 真正保护发布，需要先把它写成可预测的工程资产。

<!-- more -->

## 先限定测试边界

E2E 不适合覆盖所有分支。它最值得放在高价值用户路径上，例如登录、下单、支付回调后的状态刷新、权限切换、报表导出。按钮颜色、纯函数逻辑和组件内部边界，更适合交给单元测试或组件测试。

| 场景 | 推荐测试层级 | 原因 |
| --- | --- | --- |
| 价格计算、权限判断 | 单元测试 | 输入输出明确，速度快 |
| 表单校验、弹窗交互 | 组件测试 | 不需要真实后端 |
| 登录后创建订单 | E2E | 需要验证多页面和真实链路 |
| 第三方支付成功回跳 | E2E + Mock | 关键路径，但外部系统要隔离 |

一个项目初期可以只保留 5 到 10 条冒烟 E2E。它们要少而关键，失败时能直接说明“这次发布不该继续”。

## 选择器要面向用户语义

![稳定选择器与等待](/images/playwright-e2e-testing-checklist/selectors-waits.jpeg)

脆弱用例常见写法是用 CSS 层级找元素，例如 `.container > div:nth-child(2) button`。这种选择器和页面实现绑得太紧，UI 稍微重排就会失败。更稳定的做法是优先使用用户能感知的语义：角色、名称、标签、占位提示；只有确实没有语义时，再用明确的 `data-testid`。

```ts
import { test, expect } from "@playwright/test";

test("user can create an order", async ({ page }) => {
  await page.goto("/orders/new");

  await page.getByLabel("商品名称").fill("monitor");
  await page.getByLabel("数量").fill("2");
  await page.getByRole("button", { name: "提交订单" }).click();

  await expect(page.getByRole("heading", { name: "订单已创建" })).toBeVisible();
});
```

这段用例同时约束了可访问性。如果按钮没有正确名称，测试失败并不是噪音，而是在提醒页面对键盘、读屏器和自动化工具都不够友好。

## 等待状态，不等待时间

`waitForTimeout(3000)` 看似能修复偶发失败，实际是在把不确定性藏起来。机器慢一点仍然会超时，机器快一点又白白浪费三秒。稳定等待应该绑定业务状态：元素可见、URL 改变、接口返回、列表出现目标数据、按钮从禁用变成可点击。

```ts
await page.getByRole("button", { name: "保存" }).click();

await expect(page.getByText("保存成功")).toBeVisible();
await expect(page.getByRole("button", { name: "保存" })).toBeEnabled();
```

如果某个操作依赖接口，也可以等待响应，但不要把测试写成只认某个毫秒数：

```ts
const response = page.waitForResponse(
  resp => resp.url().includes("/api/orders") && resp.status() === 201
);

await page.getByRole("button", { name: "提交订单" }).click();
await response;
```

等待的目标越接近用户最终看到的状态，用例越不容易被实现细节扰动。

## 测试数据必须可回收

很多 E2E 不稳定来自脏数据：昨天创建的用户还在、订单编号重复、测试账号被别人改了权限。可维护的做法是让每条用例拥有自己的数据，并在结束后清理，或者使用一次性命名空间。

```ts
const runId = `e2e-${Date.now()}`;

test.beforeEach(async ({ request }) => {
  await request.post("/api/test/users", {
    data: { email: `${runId}@example.test`, role: "buyer" }
  });
});

test.afterEach(async ({ request }) => {
  await request.delete(`/api/test/runs/${runId}`);
});
```

测试辅助接口不应该暴露在公网，也不应该和生产环境共用。它的价值是快速创建确定状态，让 E2E 关注用户路径，而不是把半篇用例都花在准备数据上。

## CI 失败要留下证据

![CI 失败诊断](/images/playwright-e2e-testing-checklist/ci-debugging.jpeg)

CI 上的失败最难复现，所以必须自动留下截图、视频、trace 和服务端日志关联 ID。只给一个红色任务名，等于没有诊断材料。

推荐在 CI 中开启失败时留证，并把产物上传：

```ts
export default defineConfig({
  use: {
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure"
  },
  retries: process.env.CI ? 2 : 0
});
```

重试只能缓解环境抖动，不能用来掩盖真实缺陷。若一条用例经常靠重试通过，就应该回到选择器、等待条件、数据隔离和服务稳定性上排查。

## 落地清单

把 E2E 写稳，不是多加几条断言，而是减少不可控变量。上线前可以按这份清单检查：

1. 只覆盖关键用户路径，不追求全量分支。
2. 优先使用角色、标签、文本等语义选择器。
3. 等待页面状态和接口结果，不等待固定秒数。
4. 每条用例使用独立测试数据，并能清理或隔离。
5. CI 失败自动保存截图、视频、trace 和日志关联信息。

当这些约束变成默认写法，E2E 就不再只是“发布前跑一下”的心理安慰，而会成为能持续发现回归、解释失败原因、帮助团队更快发布的质量闸门。
