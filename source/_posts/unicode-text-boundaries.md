---
title: Unicode 文本别按“字符”想当然：字节、码点、字素簇与安全截断
date: 2026-07-12 03:16:32
tags:
  - Unicode
  - 字符编码
  - JavaScript
  - 工程实践
categories:
  - 工程实践
---

![Unicode 文本处理边界](/images/unicode-text-boundaries/cover.jpeg)

昵称限制 20 个“字符”、数据库字段最多 64 字节、前端把字符串截成 10 位——这些看似简单的需求，遇到重音符号、家庭 Emoji 或不同语言时就可能失效。问题不在 Unicode 太复杂，而在系统没有说清自己究竟在数什么。把文本的计量单位和规范化时机写进契约，才能避免乱码、半个 Emoji 和重复账号。

<!-- more -->

## 一个“字符”可能有四种长度

同一段文本在不同层次有不同长度，不能用一个 `length` 处理所有场景。

| 单位 | 含义 | 适合解决的问题 |
| --- | --- | --- |
| UTF-8 字节 | 编码后的存储单元 | 报文、文件、存储上限 |
| UTF-16 码元 | JavaScript 字符串内部单位 | 与底层 API 对接 |
| Unicode 码点 | `U+0000` 到 `U+10FFFF` 的编号 | 字符分类、协议处理 |
| 字素簇 | 用户感知的一个完整字符 | 昵称长度、光标、截断 |

以 `Aé👨‍👩‍👧‍👦` 为例，家庭 Emoji 由多个码点和零宽连接符组合而成：

```js
const text = "Aé👨‍👩‍👧‍👦";

new TextEncoder().encode(text).length; // 28 个 UTF-8 字节
text.length;                           // 13 个 UTF-16 码元
[...text].length;                      // 9 个 Unicode 码点

const segmenter = new Intl.Segmenter("zh-CN", {
  granularity: "grapheme",
});
[...segmenter.segment(text)].length;   // 3 个字素簇
```

因此，`text.length <= 20` 只表示“最多 20 个 UTF-16 码元”，并不等于用户能看到 20 个字符。

## 截断要沿着字素边界

![文本的多层计量单位](/images/unicode-text-boundaries/text-layers.jpeg)

`slice(0, limit)` 可能切开代理对、组合附加符号或 Emoji 连接序列。界面上的省略、头像首字和通知预览，应沿字素边界处理：

```js
function truncateGraphemes(input, limit, locale = "zh-CN") {
  if (!Number.isInteger(limit) || limit < 0) {
    throw new RangeError("limit must be a non-negative integer");
  }

  const result = [];
  const segments = new Intl.Segmenter(locale, {
    granularity: "grapheme",
  }).segment(input);

  for (const { segment } of segments) {
    if (result.length === limit) break;
    result.push(segment);
  }
  return result.join("");
}
```

运行环境若没有字素分段能力，应选用遵循 Unicode 分段规则的成熟库，而不是自己用正则枚举 Emoji。规则会覆盖肤色修饰符、国旗和组合字符，手写清单很容易漏。

## 看起来相同，不代表底层相同

![Unicode 规范化流程](/images/unicode-text-boundaries/normalization-flow.jpeg)

`é` 既可以是单个码点 `U+00E9`，也可以由 `e` 与组合重音 `U+0301` 构成。两者显示相同，直接比较、哈希或建立唯一索引却可能得到不同结果：

```js
const composed = "\u00E9";
const decomposed = "e\u0301";

composed === decomposed; // false
composed.normalize("NFC") === decomposed.normalize("NFC"); // true
```

一般文本可在输入边界统一为 NFC，再用于搜索键、缓存键和唯一性判断。原始输入若有审计或展示价值，可以另行保留。不要对签名原文、协议要求逐字节一致的载荷随意规范化；密码和外部标识也应先定义明确策略。NFC 只解决等价编码，不会自动处理大小写、易混淆字符或全半角差异。

## 把计量规则写进接口契约

“最多 30 个字符”对调用方没有可执行含义。接口、前端和数据库应共享同一份规则：

| 场景 | 建议契约 | 校验位置 |
| --- | --- | --- |
| 昵称显示 | 最多 30 个字素簇，输入转 NFC | 前端提示，后端兜底 |
| 消息报文 | UTF-8 编码后不超过指定字节数 | 服务端编码后校验 |
| 用户名唯一 | NFC 加明确的大小写策略 | 写入前处理，数据库唯一约束 |
| 非法 UTF-8 | 明确拒绝或替换，禁止静默混用 | 解码入口 |

长度检查与存储限制要使用同一单位。否则前端按码点放行，后端按字节拒绝；或者数据库截断后留下无法解码的数据。规范化也应只在确定的入口做一次，避免不同服务各自转换。

## 用边界样本守住回归

测试不要只放中文和 ASCII。至少覆盖空字符串、组合重音、CJK、肤色 Emoji、家庭 Emoji、国旗、规范化前后两种写法，以及恰好达到限制和超出一个字素的情况。对每个样本同时断言 UTF-8 字节数、字素数、截断结果和持久化往返结果。

Unicode 工程的关键不是记住所有编码细节，而是先回答三个问题：当前边界按什么单位计数，比较前采用什么规范化策略，异常输入由谁拒绝。答案一旦进入契约、公共工具和测试，文本处理就不再依赖“一个字符应该就是一位”的运气。
