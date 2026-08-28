---
title: 内存越界别等线上崩溃：用 AddressSanitizer 抓住 C/C++ 隐蔽错误
date: 2026-08-29 03:04:54
tags:
  - C/C++
  - AddressSanitizer
  - 内存安全
  - 调试
categories:
  - 工程实践
---

![AddressSanitizer 内存错误诊断](/images/addresssanitizer-memory-bugs/cover.jpeg)

C/C++ 的越界写入、释放后使用往往不会在错误发生处立刻崩溃：被破坏的数据可能经过许多调用后才引发异常，Core Dump 留下的栈也只是最后一站。AddressSanitizer（ASan）通过编译期插桩，把这类错误尽量变成发生位置明确、可以重复验证的测试失败。

<!-- more -->

## ASan 能发现什么

编译器会在内存访问周围插入检查，运行时则用影子内存记录哪些地址可访问。一次读写碰到被标记的区域，ASan 会报告访问类型、调用栈，以及相关内存的分配或释放位置。

| 错误类型 | 常见来源 | ASan 的价值 |
| --- | --- | --- |
| 堆、栈或全局区越界 | 循环边界、长度计算错误 | 在首次非法访问处停止 |
| 释放后使用 | 悬空指针、异步生命周期错位 | 同时给出释放栈与访问栈 |
| 重复释放、非法释放 | 所有权不清、异常路径重复清理 | 指向错误的释放操作 |
| 内存泄漏 | 忘记释放所有权资源 | 在支持泄漏检测的环境中汇总 |

![影子内存与受保护区域](/images/addresssanitizer-memory-bugs/shadow-memory.jpeg)

ASan 不是静态分析器：代码路径没有被测试执行，它就没有机会观察错误。因此，覆盖率和稳定的复现输入仍然重要。

## 用最小样例读懂报告

下面的循环在 `i == 3` 时写到已分配数组的下一格：

```c
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int *items = malloc(3 * sizeof(*items));
    if (items == NULL) return 1;

    for (size_t i = 0; i <= 3; ++i) {
        items[i] = (int)i;
    }

    printf("%d\n", items[0]);
    free(items);
    return 0;
}
```

用 Clang 或 GCC 编译并运行：

```bash
cc -O1 -g -fsanitize=address -fno-omit-frame-pointer demo.c -o demo
./demo
```

`-g` 保留源码位置，较低优化级别让报告更容易对应代码，保留帧指针则有助于得到完整调用栈。报告中先看错误类型和读写字节数，再看第一段属于自己项目的栈；随后确认目标地址位于哪个分配区，以及那块内存在哪里创建。不要被长篇影子字节说明带偏。

ASan 通常在发现首个错误后终止。把循环条件改成 `i < 3`，重新编译并再次跑完整测试；第一个越界可能掩盖后面的释放后使用，所以“修一个、重跑一次”比一次收集所有现象可靠。

## 接入真实项目与 CI

不要只给单个复现文件加参数。以 CMake 为例，可以为测试目标同时加入编译和链接选项：

```cmake
option(ENABLE_ASAN "Build tests with AddressSanitizer" OFF)

if(ENABLE_ASAN)
  target_compile_options(app_tests PRIVATE
    -fsanitize=address -fno-omit-frame-pointer)
  target_link_options(app_tests PRIVATE -fsanitize=address)
endif()
```

CI 单独建立 ASan 任务，使用与普通测试相同的输入，并把非零退出视为失败。集成测试、协议解析器和模糊测试尤其适合放进这个任务；报告应作为构建产物保存，避免日志截断后只剩一句失败。

## 明确工具边界

插桩会增加内存占用和运行时间，也会改变内存布局与时序，所以它适合作为开发和 CI 构建，而不是替代生产监控。未初始化读取更适合 MemorySanitizer，数据竞争交给 ThreadSanitizer，整数溢出等未定义行为可用 UndefinedBehaviorSanitizer；不同 Sanitizer 能否组合取决于工具链，ASan 与线程检测通常应拆成独立任务。

## 落地清单

- 编译与链接阶段都启用 ASan，并保留调试信息和帧指针。
- 用接近真实的输入运行单元、集成与模糊测试。
- 从第一处项目代码栈开始定位，修复后完整重跑。
- 不把“ASan 没报错”理解成路径未覆盖时也安全。
- 将 Sanitizer 任务与发布构建分开，并持续保存诊断报告。

ASan 的真正价值不是多一个编译开关，而是把随机崩溃变成可定位、可回归的失败。越早让危险访问在测试环境中停下来，留给线上偶然性的空间就越小。
