---
title: JavaScript 事件循环：微任务、宏任务与渲染时机
date: 2026-10-29 03:04:39
tags:
  - JavaScript
  - 浏览器
  - 性能
categories:
  - 工程实践
---

![浏览器事件循环封面](/images/javascript-event-loop-microtasks-rendering/cover.jpeg)

页面“偶尔卡一下”、日志顺序和直觉不一致，很多时候不是代码执行得太慢，而是任务被放进了不同的队列。理解浏览器事件循环，可以把“异步玄学”还原成可观察的调度规则。

<!-- more -->

## 一轮循环到底做什么

浏览器主线程一次只执行一个 JavaScript 栈。当前任务运行到栈清空后，事件循环才会进入下一步。可以把一轮循环简化成四步：

1. 从某个任务源取出一个已经就绪的任务，例如脚本、定时器回调、点击事件或网络回调。
2. 执行任务，直到同步代码返回；任务中创建的 Promise 回调不会插队到当前同步代码中间。
3. 清空微任务队列。只要微任务执行过程中又加入微任务，就继续清空，直到队列为空。
4. 浏览器在合适的时机处理样式、布局、绘制，然后进入下一轮。

“宏任务”是社区常用的方便说法，规范更常称为 task。不同任务源不保证一个全局的严格先后顺序，因此不要用“所有定时器都排在所有网络回调后面”这样的规则推理。真正稳定的是：当前任务不会被打断，微任务会在任务结束后被清空。

![任务队列与微任务队列](/images/javascript-event-loop-microtasks-rendering/queues.jpeg)

## 用最小实验确认顺序

下面的代码适合直接粘贴到控制台。`queueMicrotask`、`Promise.then` 和 `await` 后的续体都属于微任务；`setTimeout` 会创建一个后续任务。

```js
console.log('script-start');

setTimeout(() => console.log('timer'), 0);

queueMicrotask(() => {
  console.log('microtask-a');
  queueMicrotask(() => console.log('microtask-b'));
});

Promise.resolve().then(() => console.log('promise'));

console.log('script-end');
```

典型输出是：

```text
script-start
script-end
microtask-a
promise
microtask-b
timer
```

同步代码先完成，所以 `script-end` 早于所有回调。微任务按照加入顺序执行，且会把新加入的 `microtask-b` 一并清空，最后才轮到定时器。把 `setTimeout(..., 0)` 理解成“马上执行”会误导排查：它只表示计时器尽早变为可运行任务，仍要等待当前任务和微任务队列结束。

## 微任务为什么会让页面卡住

微任务具有“清空为止”的特性。下面的递归不会让出主线程，定时器和绘制都可能长期得不到机会：

```js
function starve() {
  queueMicrotask(starve);
}
starve();
```

生产代码里更常见的是循环批量处理、Promise 链或反复触发的观察器。处理大量数据时，要主动设置让出点，把工作拆成多个任务：

```js
function processInChunks(items, consume, chunkSize = 200) {
  let index = 0;
  function runChunk() {
    const end = Math.min(index + chunkSize, items.length);
    while (index < end) consume(items[index++]);
    if (index < items.length) {
      setTimeout(runChunk, 0); // 让输入和绘制获得机会
    }
  }
  runChunk();
}
```

如果任务是视觉更新，优先把写 DOM 的动作集中到 `requestAnimationFrame` 回调中；它会在浏览器准备下一帧时调用。计算和 DOM 写入仍要控制单次耗时，否则排进下一帧也无法消除长任务。

## 渲染不是每轮循环的承诺

事件循环完成任务和微任务后，浏览器“可能”进行渲染。页面不可见、没有视觉变化或系统繁忙时，浏览器可以跳过这一轮绘制。因此下面的代码不能保证用户看到中间状态：

```js
status.textContent = '准备中';
await Promise.resolve();
status.textContent = '完成';
```

`await` 只把后半段放入微任务；浏览器通常会在微任务清空后才寻找绘制机会，中间文字可能从未出现在屏幕上。需要确保至少跨过一个渲染机会时，可以等待两帧或使用一个任务让出主线程：

```js
status.textContent = '准备中';
await new Promise(requestAnimationFrame);
status.textContent = '完成';
```

实际选择取决于需求：只想让出主线程，用任务切分；要同步下一帧的视觉变化，用 `requestAnimationFrame`；要等待布局尺寸，读取布局属性前先完成必要的 DOM 写入，并注意读取会触发同步布局。

![从脚本到屏幕的渲染阶段](/images/javascript-event-loop-microtasks-rendering/rendering.jpeg)

## 排查异步顺序的四个动作

先给每个回调打点，不要凭函数名猜测队列。记录 `performance.now()`、事件名称和批次编号，能看出长任务和队列堆积：

```js
const mark = label => console.log(label, performance.now().toFixed(2));

new PerformanceObserver(list => {
  for (const entry of list.getEntries()) {
    console.log('longtask', entry.duration.toFixed(1), entry.name);
  }
}).observe({ type: 'longtask', buffered: true });
```

接着把问题缩小成一个最小复现：分别替换为同步代码、微任务和定时器，比较日志顺序。然后检查微任务是否在循环中持续追加，重点关注 Promise 链、`MutationObserver` 和 `queueMicrotask`。最后在 DevTools Performance 面板录制一次点击或输入，查看主线程上的长任务、帧间隙与事件回调，确认“慢”究竟发生在 JavaScript、布局还是绘制。

掌握这套模型后，异步代码的设计目标会更具体：同步任务尽快结束，微任务只做短小的收尾，重活分块并主动让出主线程，视觉更新交给帧调度，诊断则用时间戳和录制结果验证，而不是依赖“零延迟”或某个浏览器的偶然顺序。
