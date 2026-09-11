---
title: CSS 层叠上下文：为什么 z-index 很大仍会被遮挡
date: 2026-09-12 03:05:59
tags:
  - CSS
  - 浏览器
  - 前端调试
categories:
  - 前端工程
# MiniMax 返回额度耗尽（2056）；补齐两张配图并验证 JPEG 后再发布。
published: false
---

![不同界面分组中的弹层与遮挡关系](/images/css-stacking-context-z-index/cover.jpeg)

下拉菜单明明写了 `z-index: 9999`，却依然藏在工具栏下面。继续加零可能毫无作用，因为浏览器并不把整页元素放进同一张数字排行榜。要修好这类问题，先找出元素属于哪个层叠上下文，再判断应该调整哪一层。

<!-- more -->

## 一、用最小页面复现遮挡

把下面代码保存为 HTML 文件，用浏览器打开。橙色菜单与深色工具栏重叠的部分，会被工具栏盖住。

```html
<!doctype html>
<meta charset="utf-8">
<style>
  body { margin: 0; }
  .panel {
    position: relative;
    z-index: 1;
    height: 140px;
    background: #d9eeec;
  }
  .popup {
    position: absolute;
    z-index: 9999;
    left: 20px;
    top: 20px;
    width: 220px;
    height: 100px;
    background: #ffb56b;
  }
  .bar {
    position: fixed;
    z-index: 2;
    inset: 0 0 auto;
    height: 60px;
    color: white;
    background: #24364b;
  }
</style>
<section class="panel">
  <div class="popup">菜单</div>
</section>
<header class="bar">工具栏</header>
```

### 先比较父层，再比较子层

`.panel` 创建了一个层叠上下文，`.popup` 属于它的内部。浏览器在外层比较的是 `.panel` 的 `1` 和 `.bar` 的 `2`；菜单的 `9999` 无法改变这次排序。一个层叠上下文在父上下文中作为整体参与绘制，内部后代不能独自插到外部兄弟层之间。[CSS 绘制顺序规范](https://www.w3.org/TR/CSS2/zindex.html)

![层叠上下文中的父层排序与内部排序](/images/css-stacking-context-z-index/context-layers.jpeg)

## 二、沿祖先链找出隐藏的边界

层叠上下文不只由显式的层级数字产生。下面列出常见触发条件，排查时需要同时检查菜单和遮挡物的祖先。

| 样式或布局条件 | 是否创建层叠上下文 |
| --- | --- |
| `relative` / `absolute`，且 `z-index` 非 `auto` | 是 |
| `fixed` / `sticky` | 是，即使层级是 `auto` |
| `opacity` 小于 `1` | 是 |
| `transform` 非 `none` | 是，包括 `translateZ(0)` |
| `isolation: isolate` | 是 |
| flex / grid 子项，且 `z-index` 非 `auto` | 是，无须设置定位 |

因此，给容器补一行 `transform`，可能改变菜单与其他组件的遮挡关系；`position: relative` 配合默认的 `z-index: auto` 本身则不会创建层叠上下文。完整条件可查阅 [MDN 层叠上下文文档](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Positioned_layout/Stacking_context)。

### 一个可以立即验证的修复

在示例末尾追加 `.panel { z-index: auto; }`，菜单便能盖住工具栏：这里的面板不再创建上下文，菜单可以在外层参与排序。这个结论依赖示例中的其他样式；如果面板还有 `opacity: .9`，仅修改 `z-index` 就不够。

## 三、让修复符合组件的用途

| 场景 | 调整方式 | 需要检查的影响 |
| --- | --- | --- |
| 祖先的层叠上下文没有必要 | 移除触发它的样式 | 动画、透明度、定位是否改变 |
| 整组内容都应在上层 | 调整祖先的层级 | 组内其他元素也会一起升高 |
| 菜单需要跨组件展示 | 挂载到统一的弹层容器 | 定位坐标、样式继承与焦点管理 |
| 模态对话框 | 使用 `dialog.showModal()` | 名称、初始焦点与关闭后的焦点 |

统一弹层容器仍须检查祖先链，挂到 `body` 并不意味着自动进入浏览器顶层。对模态交互，可以把下面片段追加到示例中：

```html
<button id="launch">打开对话框</button>
<dialog id="modal" aria-labelledby="modal-title">
  <h2 id="modal-title">操作确认</h2>
  <form method="dialog">
    <button autofocus>关闭</button>
  </form>
</dialog>
<script>
  const modal = document.querySelector("#modal");
  document.querySelector("#launch").addEventListener("click", () => {
    modal.showModal();
  });
</script>
```

`showModal()` 会把对话框放入当前文档的顶层，并让同一文档中对话框之外的内容不可交互。普通页面元素无法单靠增加 `z-index` 盖住它；但在 iframe 内打开的对话框仍受该文档边界限制。仅设置 `open` 属性或调用 `show()` 不会获得同样的模态行为。[MDN showModal 文档](https://developer.mozilla.org/en-US/docs/Web/API/HTMLDialogElement/showModal)

## 四、按可观察的现象排查

在开发者工具中选中被遮挡元素，沿祖先链记录定位、层级、透明度和变换，再对遮挡物做同样检查，找到两条链首次分开的层叠上下文。一次只关闭一个可疑属性，观察重叠区域是否变化。

还要区分遮挡与裁剪：如果菜单恰好在容器边缘消失，应检查 `overflow: hidden` 等裁剪条件；单纯提高层级不能取消裁剪。修复后同时验证滚动位置、窄屏布局、键盘焦点和关闭行为，避免视觉恢复正常后，交互仍留在错误的位置。
