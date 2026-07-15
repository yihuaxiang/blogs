---
title: 把文件写稳：原子替换、fsync 与崩溃一致性
date: 2026-07-16 03:05:59
tags:
  - 文件系统
  - 可靠性
  - Python
categories:
  - 工程实践
---

![原子文件写入与可靠存储](/images/atomic-file-writes-crash-consistency/cover.jpeg)

配置、缓存索引和本地状态看似只是“小文件”，却常在进程被杀、机器断电或两个实例同时写入时变成半截 JSON。可靠写入的关键不是多调用一次 `write`，而是分清原子可见性与持久化，并按正确顺序提交数据。

<!-- more -->

## 一次“写成功”到底承诺了什么

普通的直接覆盖通常包含打开、截断、写入三个动作。若进程在截断后退出，旧内容已经消失，新内容却可能只写了一半。即使 `write` 返回成功，数据往往也只是进入内核页缓存，尚未抵达稳定存储。

可靠性可以拆成三个层次：

| 目标 | 含义 | 主要手段 |
| --- | --- | --- |
| 完整性 | 读者只能看到完整旧版或完整新版 | 同目录临时文件 + 原子替换 |
| 数据持久化 | 新文件内容在掉电后仍可恢复 | 对临时文件执行 `fsync` |
| 名称持久化 | 替换后的目录项在掉电后仍存在 | 替换后对父目录执行 `fsync` |

`rename` 或 `os.replace` 解决的是“切换瞬间不可分割”，并不自动等于数据已经落盘。把两者混为一谈，是许多“偶现配置损坏”的根源。

![临时文件到正式文件的原子切换](/images/atomic-file-writes-crash-consistency/atomic-swap.jpeg)

## 正确的提交顺序

一个适用于 Linux 本地文件系统的基本流程是：

1. 在目标文件的同一目录创建临时文件；
2. 写完内容，刷新语言运行时缓冲，再对文件执行 `fsync`；
3. 用原子替换把临时文件切换为目标文件；
4. 对父目录执行 `fsync`，持久化目录项变化。

临时文件必须与目标位于同一文件系统，否则替换可能退化为复制或直接失败。下面的 Python 示例把清理逻辑也包含进来：

```python
import os
import tempfile
from pathlib import Path


def atomic_write(path: str, data: bytes) -> None:
    target = Path(path).resolve()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_name, target)

        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
```

这里先 `flush` 是为了把 Python 用户态缓冲交给内核，再由 `fsync` 要求内核同步。捕获 `BaseException` 则能在中断等异常下尽量删除未提交的临时文件；清理失败不应覆盖原始异常。

## 崩溃发生在每一步会怎样

![崩溃边界与可恢复文件状态](/images/atomic-file-writes-crash-consistency/crash-boundaries.jpeg)

| 崩溃位置 | 重启后的预期状态 |
| --- | --- |
| 临时文件写完之前 | 正式文件仍是旧版，可能遗留临时文件 |
| 临时文件 `fsync` 之后、替换之前 | 旧版仍可见，临时文件内容完整 |
| 替换之后、目录 `fsync` 之前 | 运行时可见新版，但掉电后的名称持久性未完成 |
| 目录 `fsync` 之后 | 新版内容与名称均已提交 |

具体保证仍取决于操作系统、文件系统、挂载参数和硬件；NFS、对象存储挂载以及部分容器卷不能直接套用本地磁盘结论。跨平台库也应分别验证 Windows 与 Unix 的替换、权限和目录同步语义。

## 原子替换解决不了的事

### 多写者覆盖

原子操作只能避免读到半个文件，不能阻止两个写者依次提交，导致后者覆盖前者。需要读改写时，应增加进程锁，或在内容中保存版本号并使用比较后交换的协议。锁文件还要明确超时、持有者身份和进程崩溃后的恢复策略。

### 权限与元数据

新建临时文件后再替换，可能改变原文件的权限、所有者、扩展属性或 ACL。程序应明确元数据策略：安全配置通常使用固定的最小权限；确需继承时，在替换前复制并核验元数据，不要依赖默认值。

### 内容本身无效

完整写入不代表内容正确。配置文件可以加入格式版本，并在替换前解析临时内容；重要状态可附带校验和。启动时若发现残留临时文件，应依据明确规则恢复或清理，而不是随意选择时间戳最新者。

## 如何验证实现

不要只跑一次成功路径。测试应在写入、文件同步、替换和目录同步处注入异常，随后启动新的读者检查：正式路径必须始终能解析，内容只能是旧版本或新版本。再并发启动多个读者，循环读取并校验哈希，可快速暴露“先删后改名”或跨目录临时文件等错误。

若业务允许丢失最近一次更新，可以省略部分持久化成本，但应把这一点写成清晰的故障承诺。可靠文件写入不是固定模板，而是先决定能接受什么损失，再用原子替换和同步边界兑现承诺。
