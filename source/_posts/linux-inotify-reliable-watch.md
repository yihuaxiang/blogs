---
title: Linux 文件监控别只等事件：用 inotify 处理队列、重命名与溢出恢复
date: 2026-10-01 03:02:50
tags:
  - Linux
  - inotify
  - 文件系统
  - 工程实践
categories:
  - 工程实践
---

![Linux inotify 文件监控与事件队列](/images/linux-inotify-reliable-watch/cover.jpeg)

配置热加载、构建工具和同步服务都需要知道“文件变了”。轮询目录浪费 CPU，盲信通知又会在高峰期丢状态。`inotify` 只提供事件流，不保证永不丢失，也不维护目录快照。监听、去抖和重扫组合起来，文件监控才能长期运行。

<!-- more -->

## inotify 到底通知什么

`inotify_init1(IN_NONBLOCK | IN_CLOEXEC)` 返回文件描述符，应用再用 `inotify_add_watch()` 加入目录。读取时得到变长的 `inotify_event`：`wd` 是监听项，`mask` 是事件类型，`cookie` 配对重命名，`name` 是目录项名称。

监听通常放在目录上，而不是单个文件上。目录监听可以看到子项创建、删除和移入；编辑器“写临时文件再 rename 覆盖”时，原文件 inode 可能消失，盯着旧文件就会失效。递归监听必须为每个子目录建立 watch，并在发现新目录时添加。

| 事件 | 常见含义 | 处理建议 |
| --- | --- | --- |
| `IN_CREATE` | 目录中出现新项目 | 若是目录，递归加入 watch |
| `IN_CLOSE_WRITE` | 写入文件已关闭 | 适合触发解析，避免读到半成品 |
| `IN_MOVED_FROM` / `IN_MOVED_TO` | 项目被移出或移入 | 用 `cookie` 配对，识别原地改名 |
| `IN_DELETE` | 目录项被删除 | 清理缓存和对应状态 |
| `IN_Q_OVERFLOW` | 事件队列溢出 | 放弃增量假设，执行全量重扫 |
| `IN_IGNORED` | watch 已被内核移除 | 重新建立监听并刷新状态 |

同一个保存动作可能产生多次 `IN_MODIFY`，不同编辑器的序列也不同。业务层应把事件归一成“路径需要重算”，不要为每个事件执行昂贵任务。

![inotify 事件进入有界队列](/images/linux-inotify-reliable-watch/event-queue.jpeg)

## 非阻塞读取与去抖

事件描述符可以接入 `select`、`poll` 或 `epoll`。下面用 `inotify-simple` 展示循环；生产代码还要递归建 watch、记录快照并限制并发。

```python
from inotify_simple import INotify, flags

root = "/srv/config"
ino = INotify(nonblocking=True)
mask = (flags.CREATE | flags.CLOSE_WRITE | flags.MOVED_TO |
        flags.DELETE | flags.MOVED_FROM | flags.Q_OVERFLOW)
ino.add_watch(root, mask)

dirty = set()
while True:
    for event in ino.read(timeout=1000):
        kinds = flags.from_mask(event.mask)
        if flags.Q_OVERFLOW in kinds:
            dirty.add(root)             # 增量信息已经不可信
            continue
        path = f"{root}/{event.name}" if event.name else root
        dirty.add(path)

    # 以单调时钟做 100~300 ms 去抖，再批量处理 dirty
    process_ready_paths(dirty)
    dirty.clear()
```

`IN_CLOSE_WRITE` 适合作为配置解析触发点，但不能取代 `IN_MOVED_TO`：原子替换通常是临时文件写完后移入目标目录。处理路径时先做 `stat`，确认仍是普通文件，再打开并校验内容；收到事件时文件可能已经不存在。

去抖窗口应服务于业务目标：配置文件可等待几百毫秒，代码索引可按目录批处理。执行器要限制并发并允许取消旧任务，否则后台排队仍会使用旧快照。

## 队列溢出不是重试读取

每个 inotify 实例都有内核队列，容量受 `fs.inotify.max_queued_events` 影响；监听数量还受 `max_user_watches` 和 `max_user_instances` 约束。消费者暂停、批量写入或监听目录过多时，队列可能溢出。此时收到 `IN_Q_OVERFLOW`，缺少的事件无法补回，继续按增量处理会得到“看似合理但已错误”的状态。

可靠的恢复流程是：

1. 把实例标记为 `resyncing`，暂停依赖增量事件的下游任务。
2. 扫描根目录，构建新的路径到 `(类型、大小、mtime、inode)` 快照。
3. 与旧快照做差异比较；对新增、删除和变化项重新计算，必要时再做哈希。
4. 检查 `IN_IGNORED`、目录删除和权限变化，补建缺失的 watch。
5. 原子替换快照后恢复消费；重扫期间收到的事件再跑一轮轻量校验。

![事件溢出后的目录重扫与状态恢复](/images/linux-inotify-reliable-watch/recovery-scan.jpeg)

## 上线前的检查清单

首先记录队列溢出次数、watch 数、重扫耗时和待处理路径数；没有指标，故障会被误认为“偶发漏通知”。再用临时文件写入、跨目录移动、目录删除重建和批量创建做演练，确认能收敛到正确快照。监控进程也要优雅关闭：停止接收任务，等待解析完成，再关闭描述符。

`inotify` 适合做低延迟提示，不适合当作永不丢失的消息队列。把事件压缩成脏路径，以快照为事实来源，为 `IN_Q_OVERFLOW` 设计可观测的重扫路径，文件监控才能在高负载下保持可恢复。
