---
title: 用 heapq.merge 合并有序日志：惰性归并、排序前提与稳定次序
date: 2026-09-16 03:03:05
tags:
  - Python
  - 算法
  - 日志处理
categories:
  - 工程实践
---

![多条有序日志流汇入一条按时间排列的输出流](/images/heapq-merge-sorted-logs/cover.jpeg)

排查跨服务故障时，常要把几个日志文件合成一条时间线。如果文件很大，全部读进列表再排序会占用大量内存。只要每个文件内部已经按同一规则排好序，就可以用多路归并逐条输出，让内存需求主要取决于输入路数。

<!-- more -->

## 先确认每一路真的有序

`heapq.merge()` 接收多个有序输入，返回惰性迭代器，不会自动替无序输入排序。默认要求各路升序；使用 `reverse=True` 时，各路也必须预先降序。[Python 官方文档](https://docs.python.org/3/library/heapq.html#heapq.merge)

日志按写入顺序保存，并不保证事件时间递增。异步缓冲、延迟上报都可能让较早事件出现在后面。归并前应统一时间单位，并检查文件内部的排序键。

| 输入情况 | 处理方式 |
| --- | --- |
| 每个文件按同一键升序 | 直接归并 |
| 文件内部存在乱序 | 先分别排序，再归并 |
| 单文件太大，无法整体排序 | 分块排序落盘，再归并有序块 |

### 为什么只比较队首就够了

每路剩余记录的最小值必在队首，因此所有队首中的最小值就是下一条输出。取走它以后，只需从对应输入补入一条记录，再选择新的最小值。

![各路仅保留一条候选记录，选出最小值后从对应输入补位](/images/heapq-merge-sorted-logs/frontier.jpeg)

用最小堆维护这些候选，`k` 路、共 `N` 条记录在 `k >= 2` 时，比较次数上界可写为 `O(k + N log k)`。候选记录数量为 `O(k)`；实际内存还包含文件缓冲与单条记录的大小。

## 合并 JSON Lines 文件

### 明确排序键与重复值

约定每行是一个 JSON 对象，`ts_ms` 是整数形式的 Unix 毫秒时间戳。相同时间戳按命令行中的文件顺序排列，同一文件再按行号排列。这样次序可复现，也不会让 Python 尝试比较字典内容；重复记录会完整保留。

下面保存为 `merge_logs.py`，使用 Python 3 运行：

```python
import heapq
import json
import sys
from contextlib import ExitStack


def records(stream, source_id):
    previous = None
    for line_no, line in enumerate(stream, 1):
        row = json.loads(line)
        ts = row.get("ts_ms") if isinstance(row, dict) else None
        if type(ts) is not int:
            raise ValueError(f"{stream.name}:{line_no}: invalid ts_ms")
        if previous is not None and ts < previous:
            raise ValueError(f"{stream.name}:{line_no}: out of order")
        previous = ts
        yield ts, source_id, line_no, row


def main(paths):
    with ExitStack() as stack:
        streams = [
            stack.enter_context(open(path, encoding="utf-8"))
            for path in paths
        ]
        inputs = [records(stream, i) for i, stream in enumerate(streams)]
        for _, _, _, row in heapq.merge(*inputs):
            print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: merge_logs.py FILE [FILE ...]")
    main(sys.argv[1:])
```

准备两个小文件，时间戳取小整数以方便核对：

```sh
cat > a.jsonl <<'EOF'
{"ts_ms": 1000, "event": "a1"}
{"ts_ms": 3000, "event": "a2"}
EOF
cat > b.jsonl <<'EOF'
{"ts_ms": 2000, "event": "b1"}
{"ts_ms": 3000, "event": "b2"}
EOF
python3 merge_logs.py a.jsonl b.jsonl > merged.jsonl
```

输出事件依次为 `a1、b1、a2、b2`。交换文件参数，只会改变相同时间戳记录之间的次序。`ExitStack` 覆盖整个消费过程，正常结束或异常退出都会关闭已经打开的文件。

### 把输入错误当作失败

示例拒绝缺失、字符串及布尔类型的时间戳，也检查相邻时间戳是否倒退。空文件自然跳过；空行、损坏 JSON 会直接报错。不要悄悄跳过坏行，否则合并结果可能看似完整却缺少事件。

检查是边读边做的，发现乱序时可能已经输出部分结果，因此退出码非零时，不能把输出文件当作成功产物。输出路径也不能与任何输入相同：Shell 重定向会在脚本开始前截断目标文件。

## 惰性处理的实际边界

不要把结果包进 `list()`，否则仍会积累全部记录。文件很多时，还应限制同时打开的路数，通过分批归并中间文件控制句柄和缓冲开销。

首条输出需要先取得每个非空输入的候选值；一路阻塞可能拖住整体。因此，这个示例适合已写完的离线文件。实时日志需要另外定义迟到事件与等待策略，不能直接套用静态文件的排序保证。

验证时应覆盖空输入、单路、相同时间戳、完全重复记录和中途乱序。对小数据，把结果与按同一完整排序键执行的全量排序比较，同时核对记录总数。归并得到的是约定键下的顺序；不同机器的时钟偏差仍可能影响时间线，它并不证明事件之间的因果关系。
