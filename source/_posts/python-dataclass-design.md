---
title: "Python dataclass 设计指南：默认值、frozen、slots 与序列化边界"
date: 2026-10-10 03:01:49
tags:
  - Python
  - 数据建模
  - 工程实践
categories:
  - 编程语言
---

![Python dataclass 设计封面](/images/python-dataclass-design/cover.jpeg)

在 Python 项目里，配置项、消息、数据库记录经常先用字典传递，后来才发现默认值散落、字段拼写无人检查、调试输出也不稳定。`dataclasses` 提供了一条轻量的建模路径：用类型注解描述字段，让构造、比较和调试代码自动生成，同时把可变性和内存布局这些取舍显式化。

<!-- more -->

## 先把“数据”与“行为”分开

`@dataclass` 最适合表示有明确字段的值对象，而不是承载复杂副作用的服务类。下面的订单行拥有稳定的字段和一个纯计算方法，调用方不需要知道内部如何存储：

```python
from dataclasses import dataclass

@dataclass
class LineItem:
    sku: str
    unit_price: int          # 以分为单位，避免浮点误差
    quantity: int = 1

    def subtotal(self) -> int:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        return self.unit_price * self.quantity
```

生成的 `__init__`、`__repr__` 和 `__eq__` 足够覆盖大多数内部数据传输场景。字段顺序就是构造参数顺序，因此一旦对象被跨模块调用，最好使用关键字参数，降低未来新增字段时的误绑定风险。

## 默认值：工厂函数是关键

可变对象不能直接写成默认值。`items: list[str] = []` 会让所有实例共享同一个列表；`dataclasses.field(default_factory=list)` 则会在每次构造时创建独立对象：

```python
from dataclasses import dataclass, field

@dataclass
class Request:
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
```

`default_factory` 不只适用于容器，也适用于 `uuid.uuid4`、时间戳生成器等无参数函数。需要注意的是，工厂函数在实例化时执行，而不是在类定义时执行；如果默认值必须来自配置或环境变量，建议在应用启动阶段显式传入，避免对象构造暗中读取全局状态。

![默认值工厂为每个对象创建独立容器](/images/python-dataclass-design/default-factory.jpeg)

字段还可以用 `init=False` 排除在构造参数之外，并在 `__post_init__` 中计算：

```python
from dataclasses import dataclass, field

@dataclass
class UserName:
    first: str
    last: str
    normalized: str = field(init=False)

    def __post_init__(self) -> None:
        self.normalized = f"{self.first} {self.last}".strip().casefold()
```

## frozen 与 slots：分别解决什么问题

`frozen=True` 会阻止属性重新赋值，适合字典键、缓存键和跨线程传递的配置快照。但它不是深度不可变：字段里仍有列表时，列表内容仍可修改。要获得真正稳定的值对象，应使用 `tuple`、`frozenset` 等不可变容器，并在边界处完成转换。

`slots=True` 则改变实例的属性存储方式：对象不再默认携带 `__dict__`，通常能减少单个对象的内存开销，也能较早发现拼写错误的属性名。代价是不能随意附加新属性，某些依赖 `__dict__`、动态猴子补丁或特定继承布局的库也可能不兼容。不要把 `slots` 当作无条件优化，先用真实数据量和内存剖析验证收益。

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

origin = Point(0, 0)
points = {origin: "坐标原点"}
```

![frozen 与 slots 的对象边界](/images/python-dataclass-design/frozen-slots.jpeg)

## 序列化不是“自动转字典”

`dataclasses.asdict()` 会递归复制嵌套 dataclass、列表和字典，方便生成 JSON 载荷，却可能复制大对象或意外暴露内部字段。对外 API 建议定义明确的 DTO 转换函数，只挑选允许发布的字段；对日志也应主动脱敏，而不是直接把 `repr()` 当作审计格式。

```python
from dataclasses import asdict, dataclass
import json

@dataclass
class Profile:
    user_id: int
    email: str
    internal_note: str = ""

def to_public_json(profile: Profile) -> str:
    payload = {"user_id": profile.user_id, "email": profile.email}
    return json.dumps(payload, ensure_ascii=False)
```

反序列化同样需要校验：JSON 中缺字段、类型不符或出现未知字段时，应在边界返回可诊断错误，而不是直接 `Profile(**payload)` 让异常泄漏到业务深处。对于日期、枚举和金额等特殊类型，先完成解析和单位转换，再构造 dataclass。

## 选择策略与检查清单

| 需求 | 推荐做法 | 注意事项 |
| --- | --- | --- |
| 普通内部数据载体 | `@dataclass` | 关键字参数优先 |
| 容器默认值 | `field(default_factory=...)` | 工厂函数不要读取隐式全局状态 |
| 缓存键或配置快照 | `frozen=True` + 不可变字段 | frozen 不是深度冻结 |
| 大量短生命周期对象 | 评估 `slots=True` | 先测内存和第三方兼容性 |
| API 或日志输出 | 显式 DTO 转换 | 白名单字段并做脱敏 |

最后，把 dataclass 当作边界契约而非万能 ORM：字段语义、单位、可变性和序列化格式都要写进测试。这样既能享受自动生成样板代码的便利，又不会把隐含状态、共享默认值和内部字段泄露到系统其他层。
