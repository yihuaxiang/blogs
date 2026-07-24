---
title: Linux 权限别再 chmod 777：读懂目录执行位、umask 与 ACL
date: 2026-07-25 03:03:54
tags:
  - Linux
  - 文件权限
  - 系统安全
  - 故障排查
categories:
  - 工程实践
---

![Linux 文件权限与访问控制](/images/linux-file-permissions-umask-acl/cover.jpeg)

服务报出 `Permission denied` 时，最省事的处理似乎是 `chmod -R 777`。它往往能暂时消除报错，也同时把读、写、执行能力交给了所有本地用户；更麻烦的是，真正缺失的权限可能位于父目录、进程身份或 ACL，放开目标文件只是在掩盖原因。理解 Linux 如何逐层判断访问权，才能给出刚好够用的权限。

<!-- more -->

## 先分清“谁对什么做什么”

传统权限把访问者分成所有者（user）、所属组（group）和其他人（other），每组都有 `rwx` 三个位。文件与目录上的同一个字母，含义并不相同：

| 权限 | 普通文件 | 目录 |
| --- | --- | --- |
| `r` | 读取内容 | 列出目录项名称 |
| `w` | 修改内容 | 创建、删除或重命名目录项 |
| `x` | 作为程序执行 | 穿过目录并访问其中对象 |

目录的 `x` 最容易被忽略。用户即使能读取某个文件，只要路径中任意一级目录不允许其穿过，访问仍会失败。反过来，删除文件主要取决于父目录的 `w+x`，通常不取决于文件自身的写权限。

```bash
stat -c '%A %a %U:%G %n' /srv/app/config.yml
namei -l /srv/app/config.yml
id appuser
```

`stat` 查看目标元数据，`id` 确认进程用户的组，`namei -l` 则拆开整条路径。排障时先明确实际运行身份，不要用管理员 Shell 的结果代替服务现场。

## 用符号表达意图，而不是碰运气

数字权限中 `r=4`、`w=2`、`x=1`，每组相加便得到常见的 `640`、`750`。但修改权限时，符号形式更能说明意图，也不容易误伤原有位：

```bash
chmod u=rw,g=r,o= app.conf   # 仅所有者可写，组可读
chmod g+w shared.log         # 只给所属组增加写权限
chmod -R u=rwX,g=rX,o= tree  # X 仅作用于目录或本来可执行的文件
```

递归使用小写 `x` 会把普通文档也标成可执行；大写 `X` 更适合修复目录树。变更前后可用 `find tree -printf '%M %u:%g %p\n'` 抽查结果。

## umask 决定新对象的起点

![umask 创建权限的位掩码过程](/images/linux-file-permissions-umask-acl/umask-flow.jpeg)

程序创建普通文件时通常以 `666` 为上限，目录以 `777` 为上限，再由 `umask` 屏蔽不应开放的位。例如 `umask 027` 会得到文件 `640`、目录 `750`。这是按位屏蔽，不应把它理解成普通十进制减法。

```bash
umask
umask 027
touch report.txt
mkdir reports
stat -c '%a %n' report.txt reports
```

`umask` 属于进程环境，只影响之后创建的对象，不会追溯修改旧文件。服务应在其启动配置中明确设置；如果应用创建时主动指定了更窄权限，umask 也不会替它放宽。

## 从整条路径定位拒绝点

![Linux 路径逐级权限检查](/images/linux-file-permissions-umask-acl/path-check.jpeg)

假设应用无法写入 `/srv/app/logs/access.log`，不要直接递归改权限。先模拟真实身份，再逐级检查：

```bash
sudo -u appuser test -w /srv/app/logs/access.log && echo writable
namei -l /srv/app/logs/access.log
getfacl -p /srv/app/logs/access.log
```

若文件尚不存在，应测试父目录是否可写；若权限位看似足够，还要检查只读挂载、ACL 或安全模块。`sudo -u` 的测试结果只说明该项访问是否允许，不会告诉你是哪条规则造成拒绝，因此要与路径和审计日志一起判断。

## 共享目录用组和特殊位建模

多人发布目录适合用专用组，而不是开放给所有人。目录上的 setgid 位会让新对象继承目录所属组：

```bash
install -d -o root -g deploy -m 2770 /srv/releases
setfacl -m d:g:deploy:rwx,d:o::--- /srv/releases
getfacl /srv/releases
```

默认 ACL 能让新对象继续获得组权限，但 ACL 的 `mask` 会限制命名用户和组的实际权限；`getfacl` 显示的 `effective` 才是最终结果。ACL 适合少量例外，规则过多时则应回到清晰的用户组设计。对于所有人都能写、却只能删除自己文件的目录，可使用 sticky bit；`/tmp` 常见的 `1777` 就是这一模型。

## 一份最小权限排障清单

1. 确认报错操作是读、写、创建、删除还是执行，并找到真实进程用户。
2. 用 `namei -l` 检查路径每一级目录的 `x`，再看目标或父目录权限。
3. 核对所有者、补充组、ACL `mask`、挂载模式和安全模块日志。
4. 优先调整所有权或专用组，只增加完成操作所需的位。
5. 用真实身份复测，并检查新建对象是否会被 umask 或默认 ACL 改变。

权限治理的目标不是“让报错消失”，而是让访问规则可以解释：谁因哪条规则获得哪种能力。沿着身份、路径、权限位、ACL 和系统策略逐层验证，通常比 `chmod 777` 更快找到根因，也不会为下一次事故留下过大的攻击面。
