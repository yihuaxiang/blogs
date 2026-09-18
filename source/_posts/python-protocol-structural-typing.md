---
title: "Python 类型提示别只写注解：用 Protocol 做结构化类型检查"
date: 2026-09-19 03:02:09
tags:
  - Python
  - 类型系统
  - 工程实践
categories:
  - 编程语言
---

![结构化类型检查封面](/images/python-protocol-structural-typing/cover.jpeg)

Python 的鸭子类型很灵活：只要对象“看起来像”文件，就可以传给读取函数。但项目变大后，接口约定如果只存在于口头和文档里，重构时就容易变成运行期才暴露的 AttributeError。`typing.Protocol` 把这份约定写成可检查的结构，同时保留 Python 原有的低耦合和可替换性。

<!-- more -->

## 名义子类型和结构化子类型

传统继承是名义（nominal）类型：`CsvReader` 只有显式继承 `Reader`，类型检查器才会认为它满足接口。结构化（structural）类型关注的则是“有没有需要的成员”，不要求改动原类的继承关系。这很适合适配第三方 SDK、旧代码和测试替身。

```python
from collections.abc import Iterable
from typing import Protocol

class Reader(Protocol):
    def read_rows(self) -> Iterable[dict[str, str]]: ...

def import_users(reader: Reader) -> int:
    count = 0
    for row in reader.read_rows():
        save_user(row)
        count += 1
    return count
```

只要对象提供兼容的 `read_rows` 方法，就能传给 `import_users`：

```python
class MemoryReader:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    def read_rows(self) -> list[dict[str, str]]:
        return self.rows

class FileReader:
    def read_rows(self) -> Iterable[dict[str, str]]:
        yield from parse_csv("users.csv")
```

`list[...]` 是 `Iterable[...]` 的具体实现，返回类型满足协变关系，因此两个实现都兼容。编辑器可以在调用处提示拼写错误、参数不匹配和漏实现的方法，而不必等到线上才发现问题。

![Protocol 接口与多个实现的关系](/images/python-protocol-structural-typing/protocol-diagram.jpeg)

## 把 Protocol 当作边界，而不是万能基类

一个好的协议只描述调用方真正需要的最小能力。若导入流程只调用 `read_rows`，就不要把 `close`、`seek`、`reconnect` 等无关方法塞进同一个接口。接口越小，替换实现越容易，也越不容易因为第三方库升级而产生连锁修改。

可以用属性、异步方法和泛型表达更精确的边界：

```python
from typing import Protocol, TypeVar

T = TypeVar("T")

class Repository(Protocol[T]):
    def get(self, key: str) -> T | None: ...
    def put(self, key: str, value: T) -> None: ...

class HasStatus(Protocol):
    @property
    def status(self) -> str: ...
```

协议方法的参数类型仍然遵守可替换原则：实现不能把参数收窄到调用方无法保证的类型；返回值可以更具体，但不能与协议约定冲突。遇到复杂回调时，先把输入输出写成类型别名，再让 `Protocol` 组合这些小能力，通常比一个巨型接口更清晰。

## 静态检查和运行时检查是两件事

`Protocol` 的主要价值发生在静态检查阶段。把下面代码交给 mypy 或 pyright，可以在提交前发现 `BadReader` 缺少方法、返回值字段不匹配等问题：

```bash
python -m mypy src tests
# 或使用项目已经配置好的 pyright 命令
```

默认情况下，`isinstance(obj, Reader)` 并不能检查协议，因为类型注解会在运行时被擦除。如果确实需要做轻量能力探测，可以加上 `@runtime_checkable`：

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Closeable(Protocol):
    def close(self) -> None: ...

if isinstance(resource, Closeable):
    resource.close()
```

运行时检查只会判断成员是否存在，不会验证参数签名、返回值类型或方法行为。因此它适合插件发现、兼容性分支，不应替代静态检查和真正的行为测试。

![静态检查到运行时边界的流水线](/images/python-protocol-structural-typing/runtime-check.jpeg)

## 落地清单

| 场景 | 建议 |
| --- | --- |
| 应用内部稳定抽象 | 用小型 `Protocol` 描述调用方依赖 |
| 第三方对象适配 | 先写协议，再用类型检查器验证适配器 |
| 测试替身 | 让 fake 实现协议，避免复制完整基类 |
| 插件发现 | 谨慎使用 `runtime_checkable`，并补行为测试 |
| 公共库 API | 导出协议和示例实现，记录兼容范围 |

实践时可以从一个真实的函数参数开始：把它依赖的成员列出来，形成最小协议；让检查器跑通后，再逐步收紧返回值和泛型约束。这样既保留鸭子类型的灵活性，又把接口契约前移到编辑器和 CI，重构成本会明显下降。
