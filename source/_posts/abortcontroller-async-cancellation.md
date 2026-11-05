---
title: 用 AbortController 管理前端异步请求：取消传播、竞态与资源清理
date: 2026-11-06 03:03:42
tags:
  - JavaScript
  - Web API
  - 前端工程
categories:
  - 编程实践
---

![浏览器请求取消控制器示意图](/images/abortcontroller-async-cancellation/cover.jpeg)

搜索框、路由切换和上传面板都会同时发起异步任务。用户输入得很快时，旧请求可能晚于新请求返回，页面就会被过期结果覆盖；用户已经离开页面时，请求还在占用连接和解析响应。`AbortController` 提供了一条统一的取消信号，让调用方可以明确结束这些工作，并把取消原因沿着调用链传下去。

<!-- more -->

## 1. 取消的基本模型

`AbortController` 包含一个可触发取消的 `controller` 和一个只读的 `signal`。把 signal 传给支持它的 API，调用 `controller.abort()` 后，API 会收到取消通知。对 `fetch` 而言，Promise 通常会以 `AbortError` 拒绝；取消发生在响应体读取阶段时，读取也会停止。

```js
const controller = new AbortController();

try {
  const response = await fetch('/api/search?q=cat', {
    signal: controller.signal,
  });
  const data = await response.json();
  render(data);
} catch (error) {
  if (error.name === 'AbortError') return; // 用户主动取消，不当成故障
  reportError(error);
}

// 例如在组件卸载、路由离开或新请求开始时调用
controller.abort();
```

取消是协作式的：浏览器只能发通知，不能回滚已经完成的服务端写入，所以副作用仍要靠幂等键和事务收口。

## 2. 用“最新请求获胜”解决搜索竞态

只保存一个全局布尔变量通常不够：旧请求可能在检查变量后才返回。更可靠的做法是每次开始新请求时取消上一次，并给当前请求绑定一个新的控制器。

![取消信号传播到多个异步任务](/images/abortcontroller-async-cancellation/signal-flow.jpeg)

```js
let activeController = null;

async function search(keyword) {
  activeController?.abort('superseded');
  const controller = new AbortController();
  activeController = controller;

  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(keyword)}`, {
      signal: controller.signal,
    });
    const result = await response.json();

    // 即使后端忽略取消，也不允许旧任务提交界面状态
    if (activeController === controller) renderResults(result);
  } catch (error) {
    if (error.name !== 'AbortError') showSearchError(error);
  } finally {
    if (activeController === controller) activeController = null;
  }
}
```

取消减少无用工作，身份比较阻止迟到结果写入界面；`finally` 再比较一次，避免旧请求清理掉新请求的引用。

## 3. 把同一信号传给整条任务链

一个动作常包含请求、解析和渲染多个步骤。只取消最外层 `fetch` 不够，函数应显式接收 `signal`，在耗时步骤间检查状态。

```js
async function loadPreview(file, { signal }) {
  signal.throwIfAborted();
  const bytes = await file.arrayBuffer();
  signal.throwIfAborted();

  const parsed = await parseInWorker(bytes, { signal });
  signal.throwIfAborted();
  return createPreview(parsed);
}

const timeout = AbortSignal.timeout(8000);
const userAction = new AbortController();
const signal = AbortSignal.any([timeout, userAction.signal]);

loadPreview(file, { signal }).catch((error) => {
  if (error.name !== 'AbortError' && error.name !== 'TimeoutError') reportError(error);
});
```

`timeout()` 适合硬超时，`any()` 可合并超时、离开页面和手动停止。兼容旧浏览器时，用 `setTimeout` 调用 `abort` 并在清理时 `clearTimeout`。

## 4. 取消时要清理哪些资源

取消请求不等于组件自动干净；监听器、定时器、对象 URL、Worker 和流读取器都要有清理动作。

![异步任务结束后的资源清理流程](/images/abortcontroller-async-cancellation/cleanup-race.jpeg)

| 资源 | 建议清理方式 |
| --- | --- |
| `addEventListener` | 使用 `{ signal }`，取消时自动移除 |
| `setInterval` / 防抖定时器 | 保存句柄，在 `abort` 监听器中清除 |
| `URL.createObjectURL` | 预览结束或取消后调用 `URL.revokeObjectURL` |
| Worker | 监听取消并 `terminate()`，避免后台继续计算 |
| 流读取器 | 调用 `reader.cancel()`，同时关闭下游消费者 |

```js
function listenWhileAlive(element, signal) {
  const onClick = () => refresh();
  element.addEventListener('click', onClick, { signal });
  signal.addEventListener('abort', () => cancelDebounce(), { once: true });
}
```

## 5. 设计取消契约的检查清单

1. 每个可取消函数都接收 `signal`，不要从模块全局偷偷读取控制器。
2. 把取消当成正常控制流记录，区分 `AbortError`、超时和真正的网络故障。
3. 只在当前任务仍拥有写权限时更新 UI，取消检查不能替代身份校验。
4. 取消副作用型请求时，服务端仍要提供幂等和状态校验。
5. 在组件卸载、路由离开、用户开始新操作时触发取消，并释放定时器、监听器和临时文件。

让 `signal` 成为函数签名的一部分，异步任务就有了清晰生命周期：谁启动，谁结束；谁创建资源，谁回收。
