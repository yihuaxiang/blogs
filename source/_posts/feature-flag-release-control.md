---
title: 用 Feature Flag 控制发布风险：开关、灰度与回滚清单
date: 2026-06-28 03:03:29
tags:
  - Feature Flag
  - 发布工程
  - 灰度发布
categories:
  - 工程实践
---

![Feature Flag 发布控制台](/images/feature-flag-release-control/cover.jpeg)

一次发布真正危险的部分，往往不是代码合并，而是新逻辑第一次面对真实流量。Feature Flag 的价值，就是把“部署”和“启用”拆开：代码可以先安全上线，功能再按用户、环境、比例逐步打开；一旦指标异常，也能在不重新发版的情况下关闭入口。

<!-- more -->

## 先把开关当成运行时策略

Feature Flag 不是 `if` 语句的美化版，而是一套运行时决策机制。它至少要回答三个问题：谁来命中、何时生效、失败时默认走哪条路径。对于服务端功能，建议在后端完成判断，避免客户端篡改；对于纯交互优化，可以让前端读取只影响展示的开关。

![Feature Flag 决策流](/images/feature-flag-release-control/flag-flow.jpeg)

常见开关可以按生命周期拆成几类：

| 类型 | 用途 | 清理时机 |
| --- | --- | --- |
| 发布开关 | 新功能先部署后开放 | 全量稳定后删除 |
| 实验开关 | A/B 测试不同方案 | 实验结束后删除 |
| 运维开关 | 降级、限流、临时关闭能力 | 故障复盘后保留或重命名 |
| 权限开关 | 给特定租户或角色开放能力 | 跟随权限模型长期维护 |

这里最容易失控的是发布开关：它看起来临时，却经常在代码里活成永久分支。创建开关时就要写清 owner、过期时间、默认值和清理条件，否则半年后没人敢删。

## 一个可落地的数据模型

小团队不一定需要复杂平台，可以先从配置文件或数据库表开始，但字段要完整。下面是一个简化的 TypeScript 结构：

```ts
type FeatureFlag = {
  key: string;
  enabled: boolean;
  defaultValue: boolean;
  rolloutPercent: number;
  allowUsers?: string[];
  denyUsers?: string[];
  owner: string;
  expiresAt?: string;
};

function isEnabled(flag: FeatureFlag, userId: string): boolean {
  if (flag.denyUsers?.includes(userId)) return false;
  if (flag.allowUsers?.includes(userId)) return true;
  if (!flag.enabled) return flag.defaultValue;

  const bucket = hashToBucket(`${flag.key}:${userId}`);
  return bucket < flag.rolloutPercent;
}
```

这个判断逻辑有两个关键点。第一，白名单和黑名单优先级必须固定，否则排障时会出现“同一个用户为什么命中不同结果”的混乱。第二，按 `flag + userId` 做稳定分桶，不能每次请求随机，否则用户会在新旧体验之间来回跳。

## 灰度不是只改百分比

灰度发布的节奏应当和观测指标绑定，而不是靠感觉从 1% 拉到 100%。可以采用“内部账号、单个租户、低比例真实用户、核心用户、全量”的顺序，每一步都留出观察窗口。观察内容至少包括错误率、延迟、业务转化、队列堆积和客服反馈。

![灰度发布与回滚保护](/images/feature-flag-release-control/rollout-safety.jpeg)

建议把开关变更也纳入发布记录：谁在什么时间把比例从多少调到多少，相关指标是否正常，是否触发过回滚。这样复盘时不会只看到代码提交，却找不到真正改变线上行为的那一次配置修改。

## 回滚路径要提前演练

开关关闭不等于系统一定恢复。新逻辑可能已经写入新字段、触发异步任务、修改缓存结构，甚至让旧逻辑无法读取数据。因此每个重要开关都应配一段回滚说明：

```md
flag: checkout.new-pricing
关闭影响: 新计价入口停止展示，已创建订单继续按新价格结算
回滚步骤: 关闭 flag -> 暂停价格同步任务 -> 清理 10 分钟内生成的预览缓存
验证方式: 错误率回落，订单创建成功率恢复到基线
```

如果关闭开关仍需手工修数据，就不应该把它包装成“秒级回滚”。诚实记录恢复边界，能避免事故中做出错误承诺。

## 治理清单

Feature Flag 的长期成本来自数量。每周或每个迭代结束时，可以自动扫描过期开关并提醒 owner；CI 也可以阻止新增没有 owner 或过期时间的发布开关。对核心链路，还要给开关读取增加缓存和超时：配置中心短暂不可用时，系统应使用最近一次可用值，而不是把每个请求都拖进故障。

最后记住一条原则：开关负责降低发布风险，不负责掩盖设计不清。命名清楚、默认值明确、灰度可观测、回滚可执行、过期可清理，Feature Flag 才会从临时救急工具变成可靠的发布基础设施。
