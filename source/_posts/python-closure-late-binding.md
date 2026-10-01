---
title: Python 闭包为何总拿到最后一个值：晚绑定与循环回调
date: 2026-10-02 03:07:18
tags:
  - Python
  - 闭包
  - 异步编程
categories:
  - 工程实践
---

![多个延迟回调共享一个循环变量](/images/python-closure-late-binding/cover.jpeg)

给一组按钮绑定处理函数，点击后却全部打印最后一个按钮的名称；批量创建延迟任务，任务执行时又都拿到了最后一个 ID。这类问题常被误以为是异步执行顺序错乱，真正原因通常是闭包对外层变量采用晚绑定：函数创建时保存的是变量本身，调用时才读取它当前的值。

<!-- more -->

## 先用最小例子复现

```python
def make_handlers(names):
    handlers = []
    for name in names:
        handlers.append(lambda: f"open {name}")
    return handlers

handlers = make_handlers(["home", "settings", "help"])
print([handler() for handler in handlers])
# ['open help', 'open help', 'open help']
```

`lambda` 不是在循环的每一轮复制一个字符串。函数对象记录了对局部变量 `name` 的引用；`make_handlers` 返回后，三个函数仍共享同一个闭包单元。调用发生在循环结束之后，`name` 的当前值自然是 `help`。

| 写法 | 函数创建时保存什么 | 调用时读取什么 |
| --- | --- | --- |
| `lambda: name` | 外层变量的引用 | 变量当时的值 |
| `lambda name=name: name` | 默认参数的值 | 参数绑定的值 |
| `partial(open_page, name)` | 位置参数 | 已绑定的参数 |

![创建回调与真正执行之间的时间差](/images/python-closure-late-binding/timeline.jpeg)

## 三种可靠的绑定方式

### 用默认参数固定当前值

默认参数在函数定义表达式执行时求值，因此可以把这一轮的 `name` 存进新函数自己的参数槽位：

```python
def make_handlers(names):
    return [lambda name=name: f"open {name}" for name in names]

handlers = make_handlers(["home", "settings", "help"])
assert [handler() for handler in handlers] == [
    "open home", "open settings", "open help"
]
```

这里的 `name=name` 左侧是函数参数，右侧是在创建函数时求值的循环变量。若绑定的是可变对象，默认参数只保存对象引用；之后原列表被修改，回调仍会看到同一个列表。需要快照时，应显式复制，例如 `lambda item=item.copy(): ...`，并说明复制深度。

### 写成工厂函数，隔离每个闭包

工厂函数每调用一次就创建一个新的局部变量和闭包单元，语义更直观，也适合绑定多个参数：

```python
def make_handler(name, audit):
    def handler():
        audit.append(name)
        return f"open {name}"
    return handler

audit = []
handlers = [make_handler(name, audit) for name in ["home", "settings"]]
assert [handler() for handler in handlers] == ["open home", "open settings"]
assert audit == ["home", "settings"]
```

如果闭包会修改外层变量而不是调用可变对象的方法，必须使用 `nonlocal`：

```python
def counter():
    value = 0
    def next_value():
        nonlocal value
        value += 1
        return value
    return next_value
```

### 用 `functools.partial` 表达参数绑定

当回调只是给已有函数预填参数时，`partial` 比嵌套 `lambda` 更容易读和调试：

```python
from functools import partial

def open_page(name, *, source):
    return f"{source}:{name}"

handlers = [
    partial(open_page, name, source="menu")
    for name in ["home", "settings"]
]
assert [handler() for handler in handlers] == ["menu:home", "menu:settings"]
```

## 延迟任务里还要检查生命周期

修复绑定问题后，异步代码仍可能有另一类错误：任务开始执行时，外层资源已经关闭。比如把文件对象放进回调，再在 `with` 代码块外等待任务，变量值虽然正确，文件句柄却已失效。创建任务前应明确数据和资源的所有权；需要延迟读取时，先把必要内容读成不可变快照，或让任务在资源生命周期内完成。

调试闭包可以查看函数的自由变量：

```python
handler = make_handlers(["home"])[0]
print(handler.__code__.co_freevars)
print(handler.__closure__)
```

`co_freevars` 能提示函数依赖哪些外层名称，但不应把它当成业务逻辑。更实用的做法是为回调写测试：创建多个回调，分别调用并断言每个参数；再测试循环结束后修改原变量或可变对象时，结果是否符合接口约定。

## 选择方式的工程清单

| 场景 | 推荐做法 | 原因 |
| --- | --- | --- |
| 只固定一个简单值 | 默认参数 | 改动小，创建时求值 |
| 需要状态或多步逻辑 | 工厂函数 | 每个回调拥有独立闭包 |
| 给现有函数预填参数 | `partial` | 参数关系清楚，便于复用 |
| 需要数据快照 | 显式复制或序列化 | 避免后续修改穿透引用 |

看到“循环里创建回调”时，先问两个问题：回调读取的是变量引用还是创建时的值？回调执行时依赖的资源是否仍然存活？把这两个边界写进测试和接口说明，晚绑定就会从隐蔽陷阱变成可控的语言特性。
