---
title: Python 浅拷贝为何挡不住串改：别名、嵌套容器与可变默认参数
date: 2026-09-13 03:04:10
tags:
  - Python
  - 内存模型
  - 故障排查
categories:
  - 工程实践
published: false
---

![两个独立容器连接同一个可变对象的等距示意](/images/python-copy-aliasing-mutable-defaults/cover.jpeg)

处理一份订单副本，却把原订单的标签也改了；函数连续调用两次，第二次竟然带着上次的数据。这类问题不一定涉及线程竞争，仅靠对象共享就能发生。排查时，关键是看清哪些名字指向同一对象，以及一次复制究竟切断了哪层共享关系。

<!-- more -->

## 先分清“换绑定”和“改对象”

Python 的赋值会建立名字与对象的绑定，不会自动复制对象。执行 `alias = source` 后，两个名字指向同一个字典。给 `alias` 重新赋值，只会改变这个名字的绑定；执行 `alias["limit"] = 9`，则会修改两者共同引用的字典。

`==` 比较是否相等，`is` 检查是否为同一对象。两个内容相等的列表可能彼此独立；排查串改时，应检查相关容器的身份，不能只看打印结果。[Python 数据模型](https://docs.python.org/3/reference/datamodel.html#objects-values-and-types)

## 浅拷贝只新建外层容器

### 用一个嵌套字典复现

下面的代码可直接用 Python 3 运行：

```python
source = {"labels": ["new"], "limit": 2}
alias = source
copied = source.copy()

print(alias is source)                     # True
print(copied is source)                    # False
print(copied["labels"] is source["labels"]) # True

copied["limit"] = 9
copied["labels"].append("checked")
print(source)  # {'labels': ['new', 'checked'], 'limit': 2}
```

`copy()` 创建了新字典，但其中的值仍引用原来的对象。替换 `copied["limit"]` 只改变新字典的一项；对标签列表执行 `append()`，修改的却是两个字典共享的列表。常见的 `list.copy()`、列表切片和字典展开也只是浅拷贝。[copy 模块说明](https://docs.python.org/3/library/copy.html)

![两个外层容器通过连线指向同一个嵌套列表](/images/python-copy-aliasing-mutable-defaults/shared-list.jpeg)

### 按修改范围切断共享

如果函数只会添加标签，就显式复制这条修改路径：

```python
def add_label(order, label):
    result = dict(order)
    result["labels"] = list(order["labels"])
    result["labels"].append(label)
    return result

original = {"labels": ["new"], "limit": 2}
changed = add_label(original, "paid")
assert original["labels"] == ["new"]
assert changed["labels"] == ["new", "paid"]
assert changed["labels"] is not original["labels"]
```

这里假设标签是字符串；若标签本身是字典，修改其字段仍可能影响原对象。其他嵌套字段也继续共享，因此接口应明确承诺“返回新标签列表”，不能笼统声称返回完全独立的订单。

## 深拷贝仍然有共享语义

对于由普通列表、字典等构成、需要整体隔离的对象图，可以考虑 `copy.deepcopy()`。但它不会把每一次引用都复制成不同对象：单次复制会记录已处理对象，保留副本内部原有的共享关系，也能处理循环引用。

```python
from copy import deepcopy

shared = []
source = {"left": shared, "right": shared}
cloned = deepcopy(source)

assert cloned["left"] is cloned["right"]
assert cloned["left"] is not shared
cloned["left"].append("item")
assert cloned["right"] == ["item"]
assert shared == []
```

因此，深拷贝不能修正原始结构中不合预期的共享关系。自定义类还可以改变复制行为，文件、套接字等资源也不适合靠它复制。对复杂业务对象，显式构造所需字段，通常更容易说明生命周期和所有权。[深拷贝机制与限制](https://docs.python.org/3/library/copy.html#copy.deepcopy)

## 默认参数会跨调用共享对象

函数默认值在执行函数定义时求值。若写成 `def collect(item, bucket=[])`，同一个默认列表会被后续省略该参数的调用反复使用。需要每次创建新列表时，可用 `None` 表示未提供参数：[默认参数说明](https://docs.python.org/3/tutorial/controlflow.html#default-argument-values)

```python
def collect(item, bucket=None):
    if bucket is None:
        bucket = []
    bucket.append(item)
    return bucket

assert collect("a") == ["a"]
assert collect("b") == ["b"]
provided = []
assert collect("c", provided) is provided
assert provided == ["c"]
```

这个版本仍会修改显式传入的列表，这是需要说明的接口行为。不要改成 `bucket = bucket or []`：它会把调用方提供的空列表也替换掉。如果 `None` 本身是合法业务值，应另设专用哨兵表示参数缺省。

## 把复制边界写进接口

| 需求 | 做法 | 需要确认 |
| --- | --- | --- |
| 共同维护一份状态 | 直接引用 | 谁可以修改、何时修改 |
| 仅替换外层元素 | 浅拷贝 | 嵌套对象继续共享 |
| 只修改某个分支 | 逐层复制该分支 | 分支内是否还有可变对象 |
| 隔离普通容器对象图 | 深拷贝 | 内部共享关系仍然保留 |

验证接口时，同时断言输出值、输入是否被修改，以及关键嵌套对象是否共享。这样即使两份数据当前内容相同，也能及时发现后续修改会相互影响的问题。
