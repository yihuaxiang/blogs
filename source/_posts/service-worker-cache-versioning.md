---
title: Service Worker 缓存更新别靠运气：版本、激活与回滚边界
date: 2026-10-15 03:04:13
tags:
  - JavaScript
  - PWA
  - 浏览器缓存
categories:
  - 前端工程
---

![Service Worker 缓存版本管理示意图](/images/service-worker-cache-versioning/cover.jpeg)

Service Worker 让网页拥有离线能力，也带来一种很特别的故障：服务器已经发布了新代码，用户却一直运行旧页面。原因通常不是 CDN 没刷新，而是旧的 Service Worker 仍然控制着页面，或者新 Worker 激活后还保留着旧缓存。把缓存当成“下载文件夹”很容易失控；更稳妥的做法是把它当成有版本、有生命周期、可回滚的数据副本。

<!-- more -->

## 先分清三个版本

一个页面更新问题，至少涉及三种版本：

| 对象 | 作用 | 常见误区 |
| --- | --- | --- |
| Worker 脚本 | 决定拦截规则和缓存策略 | 以为改了业务 JS 就一定触发更新 |
| 缓存名称 | 标识一组静态资源 | 永远使用固定名称，导致旧文件残留 |
| 资源 URL | 浏览器实际请求的键 | 覆盖同名文件，却没有改变 URL |

浏览器会定期检查 Worker 脚本。脚本内容发生变化后，新 Worker 先进入 `waiting`，要等旧 Worker 不再控制页面才会 `activate`。因此“部署完成”与“所有打开的标签页立即使用新版本”并不是同一个时刻。

## 用版本化缓存建立边界

把版本写进缓存名，并在激活阶段删除不再需要的缓存，是最小但关键的治理手段。缓存名不要直接使用日期时间，否则每次构建都可能留下一个新副本；更适合使用构建版本或提交短哈希。

```js
const CACHE_NAME = 'app-static-2026-10-15';
const PRECACHE = [
  '/',
  '/assets/app.js',
  '/assets/app.css',
  '/offline.html'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(names => Promise.all(
      names
        .filter(name => name.startsWith('app-static-') && name !== CACHE_NAME)
        .map(name => caches.delete(name))
    ))
  );
});
```

这里有两个重要性质：`install` 失败时新 Worker 不会完成安装，激活阶段只删除明确属于本应用的旧缓存。不要写成“删除所有缓存”，因为同一域名下可能还有别的应用或运行时缓存。

![Service Worker 生命周期和缓存切换示意图](/images/service-worker-cache-versioning/cache-lifecycle.jpeg)

## 请求策略要和资源类型匹配

预缓存适合壳资源，不适合无限增长的用户数据。运行时请求可以按资源性质选择策略：

| 资源 | 推荐策略 | 原因 |
| --- | --- | --- |
| 带哈希的 JS/CSS | Cache First | URL 变化即新版本，命中稳定 |
| HTML 入口 | Network First | 优先拿到新壳，离线时再回退 |
| 用户接口 | 通常不缓存 | 避免展示过期或越权数据 |
| 图片 | Cache First + 数量上限 | 节省流量，同时控制空间 |

例如 HTML 不应盲目 `Cache First`。如果入口文件一直命中旧缓存，它可能继续引用已经被清理的脚本。一个简单的 Network First 处理可以这样写：

```js
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.mode !== 'navigate') return;

  event.respondWith(
    fetch(request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        return response;
      })
      .catch(() => caches.match('/offline.html'))
  );
});
```

`response` 只能消费一次，所以写入缓存前必须 `clone()`。缓存接口也要放进 `waitUntil` 或显式处理失败；生产代码中还应限制响应状态、大小和缓存条目数量。

## 是否立即接管：不要只看按钮

`skipWaiting()` 和 `clients.claim()` 能让新 Worker 更快接管，但它们会带来混合版本风险：当前页面的 HTML、内存状态和新 Worker 可能不是同一套发布物。若应用不能容忍这种组合，默认等待用户关闭旧页面更安全。

如果确实需要“立即更新”，建议做成可见的用户操作，并在接管后刷新页面：

```js
// 页面收到 waiting worker 后展示“新版本可用”提示
updateButton.onclick = () => {
  registration.waiting?.postMessage({type: 'SKIP_WAITING'});
};

navigator.serviceWorker.addEventListener('controllerchange', () => {
  window.location.reload();
});
```

同时在 Worker 中只响应明确消息调用 `skipWaiting()`，不要让任意页面消息都能触发切换。

![缓存版本回滚与排障示意图](/images/service-worker-cache-versioning/rollback-debugging.jpeg)

## 发布前后的检查清单

上线前确认：新资源使用内容哈希或版本化 URL；Worker 脚本本身没有被长时间 CDN 缓存；预缓存清单中的每个 URL 都能返回成功响应。上线后用无痕窗口和已打开旧页面分别验证，检查 `registration.waiting`、`controller` 和 Cache Storage 中的名称。

回滚时优先回滚 Worker 和入口文件，使它们重新指向上一套资源；不要只删除服务器上的新 JS，因为旧缓存仍可能继续返回它。必要时发布一个清理版本，显式删除错误缓存并重新预缓存稳定资源。这样，Service Worker 才是可观测、可演进的发布组件，而不是藏在浏览器里的“神秘缓存”。
