---
title: Python 导入为何出错：同名遮蔽、模块缓存与循环依赖
date: 2026-09-09 03:04:58
tags:
  - Python
  - 模块系统
  - 故障排查
categories:
  - 工程实践
---

![用放大镜检查模块之间的导入关系](/images/python-import-shadowing-circular-dependencies/cover.jpeg)

依赖明明装好了，导入却提示属性不存在；改了源码，交互终端仍执行旧逻辑；两个文件各自正常，放在一起就报错。这些现象常被归咎于环境，其实需要先回答三个问题：导入了哪个文件、是否复用了缓存、访问时模块有没有初始化完成。

<!-- more -->

## 理解导入的三个动作

对普通 Python 源码模块，导入大致经过查缓存、定位文件、执行代码三个动作。缓存 `sys.modules` 按模块名保存模块对象；命中后，通常直接复用，不会重新执行顶层语句。

关键顺序是：创建模块对象后，先放入缓存，再执行代码。这样能避免递归导入时无限创建对象，但也意味着“模块已经存在”不等于“其中所有名字都已定义”。这一顺序是理解循环导入的基础。[Python 导入机制文档](https://docs.python.org/3/reference/import.html#loading)

![模块查找、登记缓存与完成初始化的概念示意](/images/python-import-shadowing-circular-dependencies/import-cache.jpeg)

## 同名遮蔽：先确认文件来源

常规启动方式下，脚本目录通常位于模块搜索路径前部。本地文件若叫 `json.py`，便可能抢在标准库之前被找到。此时反复安装依赖解决不了问题。[模块搜索路径说明](https://docs.python.org/3/tutorial/modules.html#the-module-search-path)

在发生故障的同一解释器、同一启动方式下检查：

```python
import sys
import json

print(sys.executable)
print(getattr(json, "__file__", None))
print(sys.path[:3])
```

若路径指向业务目录里的 `json.py`，应将文件改成业务名称，并同步修改引用；重启解释器后再次确认来源。部分内建模块没有 `__file__`，不能据此认定导入失败。脚本能成功而测试失败时，也应先比较实际解释器和搜索路径。

## 模块缓存：修改文件不等于更新对象

在同一目录保存下面两个文件，再执行 `python probe.py`：

```python
# state.py
print("initializing")
items = []

# probe.py
import state
state.items.append("job")
import state as again
print(state is again, again.items)
```

输出只有一次 `initializing`，随后是 `True ['job']`。两个导入名字指向同一对象，模块级可变状态也会共享。导入时启动线程、连接数据库或修改全局配置，都会把这些副作用带给调用方。

因此，把启动动作放进显式函数，由入口调用。调试修改后优先重启进程；`__pycache__` 是磁盘字节码缓存，删除它不能替换当前进程中已加载的模块对象。[模块与编译缓存说明](https://docs.python.org/3/tutorial/modules.html)

## 循环依赖：访问发生得太早

另建一个空目录，保存以下两个文件：

```python
# order.py
from receipt import render

class Order:
    pass

# receipt.py
from order import Order

def render(order: Order):
    return "receipt"
```

执行 `python -c "import order"` 会失败。`order` 开始执行后导入 `receipt`；后者立即从 `order` 取出 `Order`，但此时类定义尚未执行，所以触发 `ImportError`，常见提示包含 `partially initialized module`。

### 把公共定义移到更底层

将两份文件替换为下面三份，让双方共同依赖领域模型：

```python
# domain.py
class Order:
    pass

# order.py
from domain import Order
from receipt import render

# receipt.py
from domain import Order

def render(order: Order):
    return "receipt"
```

现在执行 `python -c "import order; print(order.render(order.Order()))"`，应输出 `receipt`。这里的核心是让依赖方向不再形成环，而不是反复调整导入语句顺序。

![两个业务模块共同依赖底层的公共定义](/images/python-import-shadowing-circular-dependencies/break-cycle.jpeg)

函数内部导入也可以延后访问，但前提是调用时对方已经初始化完成。仅把 `from order import Order` 改成 `import order`，若随后仍在顶层访问 `order.Order`，一样可能失败；局部导入不能自动消除设计上的循环依赖。[Python 编程 FAQ](https://docs.python.org/3/faq/programming.html#what-are-the-best-practices-for-using-import-in-a-module)

## 按证据选择修复动作

| 现象 | 先检查 | 修复方向 |
| --- | --- | --- |
| 模块缺少预期属性 | 实际文件路径 | 重命名同名文件，核对环境 |
| 修改代码仍是旧行为 | 进程是否持续运行 | 重启并减少导入副作用 |
| 模块尚未初始化 | 回溯中的相互导入 | 下沉公共定义，整理依赖方向 |

修复后，用新进程分别导入关键模块，避免已有缓存掩盖顺序问题；同时保留真实入口的启动检查，确认模块导入本身不依赖业务初始化顺序。
