---
title: 前端卡顿别靠感觉：用 PerformanceObserver 定位浏览器长任务
date: 2026-08-08 03:05:41
tags:
  - JavaScript
  - 前端性能
  - PerformanceObserver
  - 调试
categories:
  - 工程实践
---

![浏览器长任务阻塞主线程](/images/performance-observer-long-task-diagnosis/cover.jpeg)

页面已经打开，接口也很快，但点击筛选、展开弹窗或输入搜索词时仍会“顿一下”。这类问题经常不在服务端，而是某段同步 JavaScript 长时间占住了浏览器主线程：输入无法处理，样式和布局不能提交，下一帧也画不出来。与其凭感觉删代码，不如先记录长任务发生的时间、持续时间和业务动作，再做有证据的优化。

<!-- more -->

## 先把“卡顿”说清楚

浏览器主线程一次只能执行一个任务。脚本计算、JSON 解析、创建大量 DOM、同步读取布局等操作若迟迟不返回，后面的点击、滚动和渲染只能排队。浏览器把持续时间超过 50ms 的主线程任务称为长任务；它是诊断阈值，不是用户体验的唯一标准。

| 用户现象 | 优先收集的证据 | 常见根因 |
| --- | --- | --- |
| 点击后迟迟没有反馈 | 长任务时间点、业务标记 | 同步过滤、序列化或初始化 |
| 列表滚动掉帧 | 性能录制、布局读写顺序 | 大量 DOM、强制同步布局 |
| 首次进入页面发闷 | 长任务数量、第三方脚本 | 解析、组件挂载、SDK 抢占 |

因此，目标不是把每个任务都压到零，而是先找出会阻塞关键交互的那一段，并建立优化前后的可比较数据。

## 用 PerformanceObserver 留下现场

![长任务阻塞输入与渲染时间线](/images/performance-observer-long-task-diagnosis/long-task-timeline.jpeg)

`PerformanceObserver` 可以在运行中订阅性能条目。先做特性检测：不同浏览器和嵌入式 WebView 的支持情况可能不同，缺少 `longtask` 时应安静降级，而不是让监控代码报错。

```js
function observeLongTasks(report) {
  if (!("PerformanceObserver" in window)) {
    return () => {};
  }

  const supported = PerformanceObserver.supportedEntryTypes ?? [];

  if (!supported.includes("longtask")) {
    return () => {};
  }

  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      report({
        type: "longtask",
        startMs: Math.round(entry.startTime),
        durationMs: Math.round(entry.duration),
        path: location.pathname,
      });
    }
  });

  observer.observe({ type: "longtask", buffered: true });
  return () => observer.disconnect();
}
```

上报时保留路由、发布版本、设备档位和持续时间即可；不要把查询参数、输入内容或完整堆栈直接送进分析系统。长任务可能连续出现，生产环境宜按页面聚合、抽样并批量发送，避免“为了观测又制造更多工作”。

## 给业务动作加上名字

长任务条目告诉你“何时卡”，却不一定告诉你“谁卡”。在筛选、排序、首屏挂载等关键边界补上 `mark` 和 `measure`，再把测量结果与长任务的时间窗口对齐，排查范围就会从整个页面缩到一个业务动作。

```js
function measureSync(name, work) {
  const startMark = `${name}:start`;
  const endMark = `${name}:end`;

  performance.mark(startMark);
  try {
    return work();
  } finally {
    performance.mark(endMark);
    performance.measure(name, startMark, endMark);
    performance.clearMarks(startMark);
    performance.clearMarks(endMark);
  }
}

const rows = measureSync("orders:filter", () =>
  orders.filter(matchesCurrentFilters),
);
```

这里的名称应稳定且有限，例如 `orders:filter`，不要拼入用户 ID 或搜索词。发现同一测量区间经常覆盖长任务后，再用浏览器的 Performance 面板录制该交互，结合调用栈确认究竟是数据处理、框架更新还是布局抖动。监控负责发现趋势，录制负责定位代码，二者不要互相替代。

## 把大任务拆成可让出的片段

![将大型前端计算拆分为可响应的小片段](/images/performance-observer-long-task-diagnosis/chunked-rendering.jpeg)

如果瓶颈来自可分割的纯计算，不要在一次点击中处理全部数据。下面的示例每完成一小段就把控制权交回事件循环，让浏览器有机会处理输入和绘制：

```js
function yieldToBrowser() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function buildRows(items, renderRow) {
  const rows = [];
  const chunkSize = 100;

  for (let index = 0; index < items.length; index += chunkSize) {
    const chunk = items.slice(index, index + chunkSize);
    rows.push(...chunk.map(renderRow));
    await yieldToBrowser();
  }

  return rows;
}
```

`chunkSize` 不是固定答案：在目标设备和真实数据量下观测，再调整到交互可接受的范围。超长列表更适合虚拟滚动；不依赖 DOM 的重计算可移到 Web Worker；布局代码则应先集中读取，再集中写入，避免反复触发同步布局。不要只把任务切碎却让总计算量继续膨胀。

| 根因类型 | 小步改法 | 验证方式 |
| --- | --- | --- |
| 一次性处理大量数据 | 分片计算或移入 Worker | 长任务数量与时长下降 |
| 渲染全部列表项 | 虚拟列表或按需分页 | 滚动时主线程更平稳 |
| 读写布局交错 | 批量读取后统一写入 | Performance 面板不再反复布局 |
| 第三方脚本抢占 | 延后加载、隔离或移除 | 首屏关键操作更早可用 |

## 用同一套场景复测

优化前先固定复现步骤、数据规模和设备条件，记录每次操作的长任务数量、最长持续时间及关键测量值；优化后在同一场景重跑。只看平均值很容易掩盖偶发尖峰，至少还要看高分位和最慢样本。若数字变好但交互仍不顺畅，继续检查网络等待、动画、布局和输入延迟，而不是执着于某一个指标。

长任务不是判决书，而是一盏定位灯。把它与业务标记、性能录制和前后对比连成闭环，前端性能优化才能从“感觉好多了”变成可复查、可回归的工程结果。
