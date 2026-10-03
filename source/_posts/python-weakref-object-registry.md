---
title: Python 弱引用实战：让对象登记表随生命周期自动清理
date: 2026-10-04 03:03:47
tags:
  - Python
  - weakref
  - 内存管理
categories:
  - 工程实践
---

![强引用与弱引用对对象生命周期的影响](/images/python-weakref-object-registry/cover.jpeg)

预览窗口已经关闭，内存中的预览对象却一直留着。排查后发现，全局字典为了按名称查找对象，也成了它们的长期持有者。Python 的弱引用可以让登记表保留查找能力，同时把对象何时结束生命交给真正使用它的业务代码。

<!-- more -->

## 先画出谁在持有对象

普通字典会强引用键和值。即使执行 `del preview`，也只是删除这个名字的绑定；只要登记表还指向对象，它就仍有存活依据。对于“列出当前打开的预览”这样的功能，登记表应该负责发现对象，窗口才负责持有对象。

弱引用不会单独阻止目标被回收。目标实际销毁后，调用 `weakref.ref` 对象会得到 `None`；回收时机取决于运行时和其他引用，删除一个变量不保证立即销毁对象。[Python 弱引用语义](https://docs.python.org/3/library/weakref.html)

## 用弱值字典登记活跃对象

### 一个可以运行的例子

`WeakValueDictionary` 弱引用其中的值：目标被回收时，对应条目会自动移除。下面用普通自定义类模拟预览对象：

```python
import gc
import weakref

class Preview:
    def __init__(self, name):
        self.name = name

registry = weakref.WeakValueDictionary()
owner = Preview("report")
registry["report"] = owner
reference = weakref.ref(owner)

assert registry.get("report") is owner
del owner
gc.collect()  # 仅为演示主动触发回收
assert reference() is None
assert registry.get("report") is None
```

不要把 `gc.collect()` 搬进每次业务请求；这里用它让实验结果便于观察。若其他列表、任务或调试变量还强引用目标，条目继续存在就是正确行为。[弱引用容器说明](https://docs.python.org/3/library/weakref.html#weakref.WeakValueDictionary)

![业务持有者释放对象后，弱引用登记表自动移除条目](/images/python-weakref-object-registry/registry-lifecycle.jpeg)

### 取一次，再检查和使用

弱引用查询允许对象已经消失，因此“查不到”应是正常分支。取出的对象存进局部变量后，这个变量会在使用期间提供强引用：

```python
def read_name(registry, key):
    obj = registry.get(key)
    if obj is None:
        return None
    return obj.name

assert read_name(registry, "report") is None
```

不要先检查 `reference() is not None`，再调用一次 `reference()` 获取对象：两次调用之间，其他线程可能释放最后的持有者。一次获取同时完成存活检查与临时持有；它不提供对象内部状态的线程安全保证。[安全获取方式](https://docs.python.org/3/library/weakref.html#weak-reference-objects)

## 按所有权选择容器

| 容器 | 弱引用的位置 | 适合用途 |
| --- | --- | --- |
| `dict` | 无 | 需要明确保留数据的映射 |
| `WeakValueDictionary` | 值 | 按名称查找仍存活的对象 |
| `WeakKeyDictionary` | 键 | 给对象附加外部元数据 |
| `WeakSet` | 元素 | 记录当前存活的对象集合 |

弱值登记表没有容量、有效期或保留承诺。若业务要求对象在十分钟内可以复用，应另设明确的强引用缓存与淘汰规则。否则，登记刚创建的临时对象却没有业务持有者，下一次查询就可能为空。

## 检查三条隐蔽的引用路径

第一，目标类型必须支持弱引用。普通自定义类实例通常支持，内置 `list`、`dict` 实例不直接支持。独立定义使用 `__slots__` 的类时，可把 `"__weakref__"` 加入槽位以启用支持。[支持范围](https://docs.python.org/3/library/weakref.html)

第二，弱键字典的值仍是强引用。如果元数据值反过来指向键对象，就形成“字典 → 值 → 键”的强引用路径，弱键无法让它自动消失。应避免这条反向引用，或把反向关联也改为弱引用。

第三，回调若通过闭包或默认参数捕获目标，也会间接延长目标生命周期。设计回收回调时，只携带名称、编号等必要信息，并逐条检查它持有的对象。

验证登记表时，分别保留和释放业务持有者，检查条目是否随回收消失，再验证缺失分支。若对象始终不消失，应沿引用路径找出仍在持有它的容器或任务；弱引用只改变指定的边，不会消除其他所有者。
