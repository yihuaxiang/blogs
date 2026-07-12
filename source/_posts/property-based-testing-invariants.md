---
title: 别只枚举样例：用 Property-Based Testing 自动寻找边界反例
date: 2026-07-13 03:07:23
tags:
  - 测试
  - Property-Based Testing
  - TypeScript
  - 工程实践
categories:
  - 工程实践
---

![基于性质的测试与自动反例搜索](/images/property-based-testing-invariants/cover.jpeg)

传统单元测试由开发者挑选几个输入，再核对预期输出。它对已知场景很有效，却容易漏掉空数组、极端整数和意外组合。Property-Based Testing（基于性质的测试）换了一个角度：先描述对所有合法输入都应成立的规则，再让工具批量生成数据、寻找反例，并把失败输入缩减到最容易理解的形态。

<!-- more -->

## 从“举例”转向“不变量”

假设要测试一个排序函数，样例测试通常写成“输入 `[3, 1, 2]`，输出 `[1, 2, 3]`”。这只能证明一个例子。更稳定的性质至少有三条：结果单调不降、长度不变、每个元素出现次数不变。

| 性质 | 要防住的问题 | 典型反例 |
| --- | --- | --- |
| 有序性 | 比较器或循环边界错误 | 相邻元素逆序 |
| 长度不变 | 元素意外丢失或增加 | 重复值被去重 |
| 多重集合不变 | 内容被替换 | 长度相同但值已变化 |

性质描述的是行为边界，而不是实现步骤。即使以后把快速排序换成归并排序，同一组性质仍然成立。

## 用生成器探索输入空间

下面以 TypeScript 的 `fast-check` 为例。生成器负责定义合法输入，`fc.property` 接收每轮数据并检查不变量：

```ts
import fc from "fast-check";

function countValues(values: number[]) {
  const counts = new Map<number, number>();
  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return counts;
}

fc.assert(
  fc.property(fc.array(fc.integer()), (input) => {
    const output = [...input].sort((a, b) => a - b);

    expect(output).toHaveLength(input.length);
    for (let i = 1; i < output.length; i += 1) {
      expect(output[i - 1]).toBeLessThanOrEqual(output[i]);
    }
    expect(countValues(output)).toEqual(countValues(input));
  }),
  { numRuns: 1000 }
);
```

生成器应贴近真实输入域。订单数量可以限制为非负整数，用户名可以组合允许的字符和长度；不要先生成任意值，再用大量 `filter` 丢弃，否则有效样本太少，测试既慢又偏。

## 收缩让失败变得可读

![反例生成与自动收缩流程](/images/property-based-testing-invariants/shrinking-flow.jpeg)

随机测试最怕只留下一个巨大、难以复现的输入。成熟框架发现失败后会自动“收缩”：缩短数组、减小数字、删除无关字段，同时保持性质仍然失败。例如最初触发缺陷的可能是几十个整数，最后缩成 `[0, 0]`。最小反例往往直接暴露错误假设。

CI 失败时应保存框架输出的 seed 和收缩路径，并把最小反例加入普通回归测试。前者用于精确重放本次搜索，后者确保这个已知缺陷以后无需依赖随机命中。

## 优先测试可组合的性质

除了直接检查结果，还可以从业务关系中提炼性质：

```ts
fc.assert(
  fc.property(fc.array(fc.integer()), (xs) => {
    const once = sortNumbers(xs);
    const twice = sortNumbers(once);

    expect(twice).toEqual(once); // 幂等性
    expect(sortNumbers([...xs].reverse())).toEqual(once); // 变形关系
  })
);
```

常见切入点包括：编码后再解码应还原原值；序列化再解析应保留语义；加密后解密应得到明文；分页结果拼接后不重不漏；对集合重新排列不应改变聚合结果。这类往返、幂等和变形关系不必手写每个精确答案，却能覆盖大量组合。

## 不要把随机性当万能药

基于性质的测试适合纯函数、解析器、状态机和有明确约束的数据转换，但不能替代示例测试。税率、权限和错误文案等明确规则，仍需要少量可读样例充当规格说明；数据库、网络和时间依赖则要先隔离，否则随机输入只会放大环境噪声。

落地时可以先选一个输入空间大、现有样例又难覆盖的函数，只写两三条高价值性质，并控制每次 CI 的运行次数。发现反例后，先确认生成的数据属于合法域，再修实现或修性质。好的性质既不会复述代码，也不会宽泛到永远通过。

Property-Based Testing 的价值不是“多跑几次随机数”，而是迫使团队说清系统永远不能破坏什么。样例负责解释需求，不变量负责拓宽边界，自动收缩负责把复杂失败变成可执行的线索；三者结合，测试才更可能在用户之前撞上那个没想到的输入。
