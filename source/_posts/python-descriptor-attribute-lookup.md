---
title: "Python 属性访问为何会绕路：描述符协议、查找顺序与可复用校验"
date: 2026-10-18 03:03:10
tags:
  - Python
  - 描述符
  - 面向对象
categories:
  - 编程语言
---

![Python 描述符与属性访问封面](/images/python-descriptor-attribute-lookup/cover.jpeg)

看似普通的 `obj.name`，背后不一定只是从字典里取值。`property`、实例方法、ORM 字段和许多校验框架都借助描述符协议介入属性访问。理解这条查找链，能解释“实例里明明有同名属性，为何读到的却是另一个值”，也能帮助我们写出可复用、行为明确的字段组件。

<!-- more -->

## 描述符是什么

只要一个对象的类型实现了 `__get__`、`__set__` 或 `__delete__` 中的任意方法，它就可以作为描述符。关键是：描述符通常放在**类属性**上，由实例访问触发协议。

```python
class Celsius:
    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance._temperature

    def __set__(self, instance, value):
        value = float(value)
        if value < -273.15:
            raise ValueError("temperature below absolute zero")
        instance._temperature = value


class Sensor:
    temperature = Celsius()

    def __init__(self, temperature):
        self.temperature = temperature
```

执行 `sensor.temperature = 20` 时，赋值并没有直接创建同名实例属性，而是调用 `Celsius.__set__`；读取时则调用 `__get__`。当通过 `Sensor.temperature` 访问时，`instance` 为 `None`，返回描述符自身便于检查元数据。

## 查找顺序决定谁覆盖谁

描述符分为两类。实现了 `__set__` 或 `__delete__` 的是数据描述符；只有 `__get__` 的是非数据描述符。对常规实例属性读取，核心顺序可以简化为：

1. 类及其基类中的数据描述符；
2. 实例的 `__dict__`；
3. 类及其基类中的非数据描述符或普通类属性；
4. 找不到时再调用 `__getattr__`。

![属性查找优先级示意图](/images/python-descriptor-attribute-lookup/lookup-order.jpeg)

这解释了一个容易困惑的现象：数据描述符优先于实例字典，所以即使手动写入 `obj.__dict__["temperature"]`，读取也仍会经过 `Celsius.__get__`。非数据描述符则可以被同名实例属性遮蔽。

| 成员形态 | 典型例子 | 能否被实例同名属性遮蔽 |
| --- | --- | --- |
| 数据描述符 | `property`、带 `__set__` 的字段 | 不能 |
| 实例属性 | `obj.__dict__` 中的值 | 不适用 |
| 非数据描述符 | 普通实例方法 | 能 |
| 普通类属性 | 常量、默认配置 | 能 |

这里描述的是默认的 `object.__getattribute__` 行为；如果类重写了 `__getattribute__`，就等于接管整个入口，必须显式委托给 `super()`，否则可能绕过描述符甚至导致无限递归。

## 方法为什么会自动绑定 self

函数对象本身实现了 `__get__`，因此定义在类中的普通函数是非数据描述符。通过实例取方法时，Python 会把函数与实例绑定，生成 bound method：

```python
class Worker:
    def run(self, job):
        return f"done: {job}"


worker = Worker()
assert Worker.run(worker, "backup") == worker.run("backup")
assert worker.run.__self__ is worker
assert worker.run.__func__ is Worker.run
```

这不是调用语法的特殊补丁，而是描述符协议的直接结果。因为函数属于非数据描述符，`worker.__dict__["run"] = other_callable` 还能遮蔽类中的方法。调试“方法突然不可调用”时，检查实例字典往往比盯着类定义更快。

## 用 `__set_name__` 复用字段校验

同一个描述符类若要服务多个字段，需要知道自己被赋给了哪个类属性。类创建完成时，Python 会调用 `__set_name__(owner, name)`，正好可以生成内部存储名。

```python
class IntegerRange:
    def __init__(self, minimum, maximum):
        self.minimum = minimum
        self.maximum = maximum

    def __set_name__(self, owner, name):
        self.public_name = name
        self.storage_name = f"_{name}"

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance.__dict__[self.storage_name]

    def __set__(self, instance, value):
        if type(value) is not int:
            raise TypeError(f"{self.public_name} must be int")
        if not self.minimum <= value <= self.maximum:
            raise ValueError(f"{self.public_name} out of range")
        instance.__dict__[self.storage_name] = value


class RetryPolicy:
    attempts = IntegerRange(1, 10)
    delay_seconds = IntegerRange(0, 300)

    def __init__(self, attempts, delay_seconds):
        self.attempts = attempts
        self.delay_seconds = delay_seconds
```

![可复用校验描述符结构图](/images/python-descriptor-attribute-lookup/reusable-validation.jpeg)

校验逻辑只写一次，每个实例的数据仍保存在各自的 `__dict__` 中。内部名加下划线避免再次触发同一个描述符；若类使用不含 `__dict__` 的 `__slots__`，则要预留对应槽位，或改用其他存储方案。

## 使用边界与调试清单

描述符适合多个类或多个字段共同复用的访问规则。如果只是给单个类的一个字段加计算或校验，`@property` 更直观；如果需要拦截大量动态名称，才考虑 `__getattr__` 或谨慎重写 `__getattribute__`。

排查属性行为时，可以按下面的顺序缩小范围：

```python
print(vars(obj))                 # 实例实际保存了什么
print(vars(type(obj)))           # 类上是否有同名成员
print(type(type(obj).field))     # 成员是否实现描述符方法
print(type(obj).mro())           # 属性可能来自哪个基类
```

最后记住一条主线：数据描述符管得最严，实例字典居中，非数据描述符允许实例覆盖。掌握这套顺序后，`property`、方法绑定和 ORM 字段就不再是各自独立的“魔法”，而是同一套属性访问协议的不同应用。
