---
title: 用 TypeScript 可辨识联合把状态机写成可检查契约
date: 2026-11-17 03:05:03
tags:
  - TypeScript
  - 类型系统
  - 状态机
categories:
  - 工程实践
---

![状态节点与类型边界](/images/typescript-discriminated-unions/cover.jpeg)

前端状态一多，最容易失控的不是颜色和布局，而是“某个字段什么时候存在”。请求成功时有 `data`，失败时有 `error`，加载中两者都没有。如果把它们写成一堆可选属性，代码能编译，运行时却仍然要猜。可辨识联合（discriminated union）把每个状态写成独立的对象，并用一个稳定字段区分它们，让编译器替我们检查状态转换和字段访问。

<!-- more -->

## 先把“可选字段”改成互斥状态

下面的类型看似灵活，实际上允许了大量无效组合：

```ts
type BadResult<T> = {
  loading?: boolean;
  data?: T;
  error?: string;
};
```

`{ loading: false }`、`{ data: 42, error: 'timeout' }` 都能通过检查。更稳的写法是让每个分支拥有自己的必填字段：

```ts
type Result<T> =
  | { status: 'idle' }
  | { status: 'loading'; requestId: string }
  | { status: 'success'; data: T; receivedAt: number }
  | { status: 'error'; message: string; retryable: boolean };
```

`status` 就是辨识字段。它的字面量值把联合拆成四条互斥路径，因此 `success` 分支天然有 `data`，`error` 分支天然有 `message`。这比“先判断字段是不是 undefined”更接近业务规则，也能避免把空字符串、零值误判成缺失。

![联合类型按状态收窄](/images/typescript-discriminated-unions/narrow.jpeg)

## 用控制流收窄，避免类型断言

消费状态时，优先使用 `switch` 或早返回。TypeScript 会沿着控制流自动收窄类型：

```ts
function render<T>(result: Result<T>): string {
  switch (result.status) {
    case 'idle':
      return '尚未开始';
    case 'loading':
      return `请求 ${result.requestId} 进行中`;
    case 'success':
      return `收到数据：${String(result.data)}`;
    case 'error':
      return result.retryable ? `失败：${result.message}（可重试）` : result.message;
  }
}
```

这里不需要 `as`，也不需要给每个属性加问号。以后新增状态时，编辑器会在 `switch` 末尾提示分支未覆盖。若想把遗漏变成明确错误，可以加入穷尽检查：

```ts
function assertNever(value: never): never {
  throw new Error(`未处理的状态: ${JSON.stringify(value)}`);
}

function toMetric<T>(result: Result<T>): string {
  switch (result.status) {
    case 'idle': return 'idle';
    case 'loading': return 'loading';
    case 'success': return 'success';
    case 'error': return 'error';
    default: return assertNever(result);
  }
}
```

当联合增加 `cancelled`，`default` 中的 `result` 就不再是 `never`，编译会直接指出遗漏位置。这样，状态模型的变化不会悄悄漏进某个页面。

## 把状态转换集中成纯函数

类型描述状态，转换函数约束事件。将两者放在一起，可以把非法操作挡在编译期：

```ts
type Event =
  | { type: 'start'; requestId: string }
  | { type: 'resolve'; data: string; at: number }
  | { type: 'reject'; message: string; retryable: boolean }
  | { type: 'reset' };

type State = Result<string>;

function reduce(state: State, event: Event): State {
  if (event.type === 'reset') return { status: 'idle' };
  if (event.type === 'start') return { status: 'loading', requestId: event.requestId };
  if (state.status !== 'loading') return state;
  if (event.type === 'resolve') return { status: 'success', data: event.data, receivedAt: event.at };
  return { status: 'error', message: event.message, retryable: event.retryable };
}
```

`resolve` 和 `reject` 只有在 `loading` 时才有意义，函数通过一次状态判断拒绝其他组合。实际项目中还可以把 `Event` 按当前状态拆成更细的映射类型，让“只能在 loading 时 resolve”成为更严格的接口约束；但多数页面先采用上述纯 reducer，就能消除共享对象被随意改写的问题。

![事件经过 reducer 产生新状态](/images/typescript-discriminated-unions/reducer.jpeg)

## 与 API 响应和组件边界配合

服务端响应不要直接暴露给组件。先在请求层把网络错误、业务错误和成功数据归一成 `Result<T>`，组件只接收一种契约。这样组件不需要知道 HTTP 状态码，也不会把旧请求的结果覆盖到新请求上：可以在 `loading` 状态里保存 `requestId`，提交结果时比较当前请求 ID。

命名上，辨识字段应当稳定、短小，并使用字符串字面量；不要用 `boolean` 组合表示多个状态，例如 `isLoading`、`hasError`、`isEmpty` 同时存在时会产生组合爆炸。状态分支的字段尽量只放该分支需要的数据，避免重新引入一堆可选属性。

## 一份落地清单

| 检查点 | 建议 |
| --- | --- |
| 状态表示 | 用 `status` 等字面量字段区分互斥分支 |
| 字段访问 | 先收窄再读取，避免无依据的类型断言 |
| 分支完整性 | `switch` 配合 `assertNever` |
| 状态更新 | 用纯 reducer 集中处理事件 |
| 异步竞态 | 在 loading 状态保存请求标识并校验 |
| 边界转换 | 在 API 层归一化，组件只消费联合类型 |

可辨识联合的价值不在于类型写得更复杂，而是把隐含的状态图变成代码契约。状态增加、字段改名或事件扩展时，编译器会把需要修改的路径列出来；运行时只需处理真正可能发生的分支，维护成本也会随之下降。
