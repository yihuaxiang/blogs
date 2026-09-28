---
title: Linux 稀疏文件：为什么看着有 64 MiB，磁盘却只用了 8 KiB
date: 2026-09-29 03:03:17
tags:
  - Linux
  - 文件系统
  - 磁盘排障
categories:
  - 工程实践
---

![稀疏文件的逻辑大小与实际占用](/images/linux-sparse-files-disk-usage/cover.jpeg)

排查磁盘时，`ls -lh` 显示文件有几十 GB，`du -h` 却说只占几 MB，未必是工具出错。它们回答的是两个不同的问题：文件有多长，以及文件系统已经为它分配了多少空间。稀疏文件正好把这两个数字拉开；弄清空洞的含义，才能正确估算复制、备份和后续写入所需的容量。

<!-- more -->

## 空洞不是一串已经写入的零

普通文件可以看作按字节编号的地址空间。程序先跳到较远的位置再写入，中间没有写过的范围就可能成为“空洞”：读取时得到零，但文件系统无需立即为这些位置分配数据块。文件末尾的位置决定逻辑大小，已分配的数据块决定实际占用。

![稀疏文件中数据块与空洞的关系](/images/linux-sparse-files-disk-usage/holes-and-blocks.jpeg)

这里有个容易混淆的细节：**读出来是零，不代表那里一定是空洞**。真正写入过的零也会读出零，却可能占用数据块。具体占用还会受到文件系统的压缩、共享块等特性影响，所以不能只靠文件内容判断。

| 命令 | 主要看到什么 | 排障时怎么用 |
| --- | --- | --- |
| `ls -lh file` | 逻辑大小 | 判断程序能访问到的文件长度 |
| `du -h file` | 已分配空间 | 粗看当前磁盘占用 |
| `du --apparent-size -h file` | 表观大小 | 与 `ls` 的逻辑大小对照 |
| `stat -c '%s %b %B' file` | 字节数、块数、每块计量单位 | 核对两个数字的来源 |

## 用 64 MiB 文件亲手验证

下面的示例只写首尾两个字节，中间留空。使用临时目录，退出这段 Shell 后自动清理：

```bash
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
python3 - "$workdir/demo.img" <<'PY'
import sys

size = 64 * 1024 * 1024
with open(sys.argv[1], "wb") as file:
    file.write(b"A")
    file.seek(size - 1)
    file.write(b"Z")
PY

ls -lh "$workdir/demo.img"
du -h "$workdir/demo.img"
du --apparent-size -h "$workdir/demo.img"
stat -c 'size=%s bytes, blocks=%b, block-unit=%B bytes' "$workdir/demo.img"
```

在常见的 4 KiB 数据块文件系统上，可能看到 `ls` 为 64 MiB、`du` 为 8 KiB；`stat` 则给出 `size=67108864`、`blocks=16`、`block-unit=512`。后两个数相乘是 8192 字节。不同文件系统或挂载方式下，占用数字可能不同，但逻辑大小与已分配空间的区别不变。

## 哪些操作会改变占用

`truncate -s 1G file` 可以把文件的逻辑长度扩到 1 GiB，通常不会立刻写满 1 GiB 数据。相反，`fallocate -l 1G file` 的用途是预分配空间，在支持的文件系统上会先占用相应块。两者都可能让 `ls` 显示同样大小，`du` 却不同。

应用随后写入空洞时，文件长度可以保持不变，已分配空间却会增长。因此，不能因为今天的 `du` 很小，就认定备份目标盘或运行盘永远只需这么多容量。写入还可能在那时才遇到空间不足。对于虚拟磁盘镜像和测试占位文件，容量监控最好同时记录逻辑大小与实际占用，并为未来可能写满的区域预留余量。如果业务要求写入时确定有空间，应评估预分配，而不是仅看文件创建时的 `du`。

复制也要检查结果。在 GNU/Linux 上，可用 `cp --sparse=always --reflink=never source.img target.img` 尝试保留长段空洞，再对源文件和目标文件分别运行 `ls -lh`、`du -h`。归档与恢复工具是否保存稀疏布局，应以一次实际恢复后的结果为准；只校验文件字节相同，无法证明空间占用也相同。

## 排障时记住三步

先用 `ls` 看逻辑长度，再用 `du` 看当前已分配空间，最后用 `stat` 核对数值。如果两个大小相差很大，检查文件是怎样创建、复制和恢复的，并预估空洞以后被写入的容量。稀疏文件节省的是**尚未分配的数据块**，不是把未来写入的数据免费存下来的魔法。
