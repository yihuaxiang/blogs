---
title: 用 .gitattributes 管住 Git 行尾：减少跨平台换行噪声
date: 2026-10-03 03:03:57
tags:
  - Git
  - 跨平台开发
  - 代码审查
categories:
  - 工程实践
---

![不同平台的源码通过仓库统一行尾](/images/git-gitattributes-line-endings/cover.jpeg)

只改了一行业务代码，差异却显示整份文件都变了？Windows 常用 CRLF，Linux 常用 LF，编辑器保存时可能悄悄转换行尾。把规则写进仓库的 `.gitattributes`，就能减少这种噪声，让审查重新聚焦实际修改。

<!-- more -->

## 一、先分清暂存区与工作区

LF 是字节 `0A`，CRLF 是 `0D 0A`。对开启文本规范化的文件，Git 在加入暂存区时把行尾转为 LF；检出到工作区时，再按规则决定是否转换成 CRLF。因此，同一文件的暂存区与工作区字节可以不同。

`core.autocrlf` 属于个人配置，很难保证所有成员一致。`.gitattributes` 随源码共享，能按路径声明规则；对于显式设置了 `text` 和 `eol` 的文件，不必依赖个人默认值。[Git 官方文档](https://git-scm.com/docs/gitattributes)解释了这两个属性的转换语义。

![工作区与暂存区之间的行尾转换](/images/git-gitattributes-line-endings/index-worktree.jpeg)

## 二、按文件用途写规则

在仓库根目录添加：

```gitattributes
* text=auto
*.txt text eol=lf
*.sh text eol=lf
*.py text eol=lf
*.md text eol=lf
*.bat text eol=crlf
*.png -text
*.jpeg -text
```

| 属性 | 加入暂存区 | 检出到工作区 |
| --- | --- | --- |
| `text eol=lf` | 规范化为 LF | LF |
| `text eol=crlf` | 规范化为 LF | CRLF |
| `-text` | 不做行尾转换 | 不做行尾转换 |

`text=auto` 让 Git 判断内容是否为文本，不代表全部文件都强制使用 LF；没有指定 `eol` 时，检出行为仍受本地配置影响。历史中已存成 CRLF 的文本也不能指望自动规则立即修复，需要后面的重新规范化步骤。

后出现的匹配规则可覆盖前面的同名属性。脚本明确设为 LF，能避免解释器把首行尾部的回车误当成路径字符；需要 CRLF 的批处理则单独声明。图片等二进制文件显式使用 `-text`，避免不必要的转换。

## 三、先诊断，再迁移

### 看实际字节与生效属性

```bash
git ls-files --eol -- demo.txt
git check-attr text eol -- demo.txt
```

第一条可能输出：

```text
i/lf    w/crlf  attr/text eol=lf    demo.txt
```

`i/` 指暂存区，`w/` 指工作区，`attr/` 指生效规则。上例说明暂存区已经是 LF，工作区仍为 CRLF；规则不会在修改配置后立即重写磁盘文件。若看到 `mixed`，应检查文件是否混用了两种行尾。[`git ls-files` 文档](https://git-scm.com/docs/git-ls-files)列出了这些字段。

### 给历史文件重新规范化

从没有业务修改的干净工作区开始，在专门分支里添加规则，然后执行：

```bash
git add .gitattributes
git add --renormalize .
git diff --cached --stat
git diff --cached --ignore-space-at-eol
git diff --cached --check
```

`--renormalize` 会对受跟踪文件重新应用 clean 处理并加入暂存区，不会自动加入其他未跟踪文件，也不会直接把当前磁盘文件全部改成 LF。仓库若配置了其他 clean 过滤器，这一步也会触发它们，应逐项检查暂存差异。[`git add` 文档](https://git-scm.com/docs/git-add)说明了处理范围。

忽略行尾空白后，源码差异通常应消失，但新增规则仍会显示；这个选项也会隐藏行末空格，不能单独证明变更只有换行。规范化变更应独立审查，避免与业务修改混合。

![先诊断再规范化最后审查的迁移流程](/images/git-gitattributes-line-endings/normalization-review.jpeg)

## 四、让编辑器配合规则

Git 管理入库和检出，编辑器负责保存时的格式。可以用 `.editorconfig` 为常规文本设置 `end_of_line = lf`，为批处理保留 CRLF，但应确认编辑器支持它。

行尾规范化不会把 UTF-16 转成 UTF-8，也不会修复孤立回车、文件权限或脚本执行位。迁移后，用干净检出分别跑脚本与构建，再检查 `git ls-files --eol`：暂存区满足规范、工作区满足用途，才算完成。
