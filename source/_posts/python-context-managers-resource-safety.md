---
title: Python 资源清理别靠记忆：用上下文管理器守住异常路径
date: 2026-09-17 03:03:22
tags:
  - Python
  - 资源管理
  - 异常处理
categories:
  - 工程实践
---

![文件、连接与锁经过统一资源入口安全流转的等距示意](/images/python-context-managers-resource-safety/cover.jpeg)

文件能正常关闭，不代表代码已经可靠：读取中途抛异常、获取第二个资源失败、函数提前返回，都可能绕过散落在各处的清理语句。Python 的上下文管理器把“获得资源”和“无论如何都释放资源”组成一个边界，让异常路径与正常路径共享同一套收尾逻辑。

<!-- more -->

## `with` 真正保证了什么

上下文管理器实现 `__enter__()` 与 `__exit__()`。进入 `with` 时先调用前者；只要进入成功，离开代码块时就会调用后者，包括正常结束、`return` 和抛出异常。最常见的文件操作因此不必手写 `close()`：

```python
def first_line(path):
    with open(path, encoding="utf-8") as file:
        return file.readline().rstrip("\n")
```

这里的重点不是少写一行代码，而是关闭动作与资源生命周期在结构上绑定。相比之下，先 `open()`、处理数据、最后 `close()` 的写法，一旦处理中断就可能泄漏文件描述符。

不过，若 `__enter__()` 自己失败，Python 不会调用同一个对象的 `__exit__()`。因此应在 `__enter__()` 内只返回“已经完整可用”的资源；若初始化分多步完成，就要在该方法内部撤销已经成功的步骤。

## 自定义管理器：不要悄悄吞掉异常

下面用锁展示协议本身。`__exit__()` 会收到异常类型、异常对象和回溯；返回 `False`（或 `None`）表示异常继续向外传播，返回真值才表示异常已被处理。

```python
class HeldLock:
    def __init__(self, lock):
        self.lock = lock

    def __enter__(self):
        self.lock.acquire()
        return self.lock

    def __exit__(self, exc_type, exc, traceback):
        self.lock.release()
        return False
```

除非管理器的职责明确包含异常恢复，否则不要返回 `True`。吞掉异常会让调用方误以为操作成功，也会使监控和测试失去失败信号。清理动作若也可能失败，应保留原始异常的上下文，避免只剩下一个含义更弱的关闭错误。

![上下文管理器在成功与异常两条路径上执行同一清理动作](/images/python-context-managers-resource-safety/cleanup-flow.jpeg)

## 用 `contextmanager` 写轻量封装

只有一段获取与释放逻辑时，`contextlib.contextmanager` 比类更紧凑。`yield` 前负责获取，`yield` 后负责释放，`try/finally` 则保证调用方代码抛异常时仍能清理：

```python
from contextlib import contextmanager

@contextmanager
def transaction(connection):
    cursor = connection.cursor()
    try:
        yield cursor
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        cursor.close()
```

异常分支中的 `raise` 很关键：回滚负责恢复数据库状态，不等于业务错误已经解决。还要明确事务的所有权——如果调用方已经开启外层事务，这个封装就不应擅自提交整个连接，通常要改用保存点或由外层统一控制。

## 资源数量动态变化时用 `ExitStack`

当文件数量由配置决定，嵌套多少层 `with` 无法预先写死。`ExitStack` 可以按成功顺序登记资源，并在退出时按相反顺序清理；若打开第三个文件失败，前两个也会关闭。

```python
from contextlib import ExitStack

def read_all(paths):
    with ExitStack() as stack:
        files = [
            stack.enter_context(open(path, encoding="utf-8"))
            for path in paths
        ]
        return [file.read() for file in files]
```

它还可以通过 `callback()` 登记没有上下文协议的清理函数，适合临时目录、旧式客户端和多阶段初始化。回调同样后进先出，这通常正好与资源依赖的拆除顺序一致。

## 根据生命周期选择工具

| 场景 | 推荐方式 | 注意边界 |
| --- | --- | --- |
| 单个内置资源 | 直接使用 `with` | 不要把句柄带出代码块继续使用 |
| 简单获取与释放 | `@contextmanager` | 异常处理后决定是否重新抛出 |
| 复杂状态或可复用对象 | 自定义类 | `__enter__()` 失败时自行回滚 |
| 动态数量的资源 | `ExitStack` | 按依赖关系登记，确认逆序清理合理 |

测试时不要只覆盖成功路径。至少注入一次块内异常、一次中途获取失败，并断言文件已关闭、锁已释放、事务已回滚。上下文管理器不是让异常消失，而是让资源生命周期在异常出现时依然可预测。
