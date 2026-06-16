---
title: 让终端更顺手的几个命令行工具
date: 2026-06-16 16:00:00
tags:
  - 命令行
  - 效率
  - 工具
  - 终端
categories:
  - 工具
---

![让终端更顺手的几个命令行工具](/images/cli-productivity-tools/cover.jpeg)

在终端里待久了会发现：真正拉开效率差距的，往往不是什么高深技巧，而是有没有用上几款更现代的小工具。它们大多是对 `grep`、`find`、`cat`、`cd` 这些老命令的「平替升级」，装上之后基本回不去了。下面这几个是我天天在用的。

<!-- more -->

## ripgrep（rg）—— 搜内容的首选

`rg` 是 `grep` 的现代替代品，用 Rust 写的，**默认递归搜索、默认尊重 `.gitignore`、速度极快**。

```bash
rg "TODO"                 # 当前目录递归搜 TODO，自动跳过 .gitignore 里的文件
rg -i "error" src/        # 忽略大小写，只在 src/ 里搜
rg -t py "def main"       # 只在 Python 文件里搜
rg -l "import requests"   # 只列出命中的文件名
```

比起 `grep -rn`，`rg` 不用记一堆参数，开箱即用还更快。

## fd —— 找文件的首选

`fd` 是 `find` 的友好版，语法简单、输出带颜色、同样默认忽略 `.gitignore`：

```bash
fd report                 # 找名字含 report 的文件/目录
fd -e md                  # 找所有 .md 文件
fd -H secret              # 连隐藏文件一起找
fd -t f -e jpg images/    # 在 images/ 下找所有 jpg 文件
```

对比一下就知道差距：`find . -name "*.md"` vs `fd -e md`。

## fzf —— 模糊查找神器

`fzf` 是一个通用的「交互式模糊筛选器」，把任何列表喂给它，就能即时模糊搜索。最常用的是配合 shell 快捷键：

- `Ctrl+R`：模糊搜索命令历史（装了 fzf 后体验完全不同）；
- `Ctrl+T`：把模糊选中的文件路径插入当前命令行；
- `Alt+C`：模糊跳转到子目录。

也能和别的命令组合，比如「模糊选一个分支切过去」：

```bash
git branch | fzf | xargs git checkout
```

或者用预览窗口边搜边看文件内容：

```bash
fzf --preview 'bat --color=always {}'
```

## bat —— 带语法高亮的 cat

`bat` 输出文件时自带**语法高亮、行号、Git 改动标记**，还会自动分页：

```bash
bat config.yml            # 高亮 + 行号查看
bat -p script.sh          # 纯净模式，不显示边框和行号
```

可以把它设成 fzf、`git` 等工具的预览器，整体观感统一。

## zoxide —— 会学习的 cd

`zoxide` 记住你常去的目录，之后只要敲关键词就能跳过去，不用打全路径：

```bash
z blog          # 跳到最近常用的、路径里含 blog 的目录
z dl proj       # 多关键词，跳到同时匹配的目录
zi              # 配合 fzf 交互式选择历史目录
```

用一阵子之后，`cd ../../some/long/path` 这种操作基本就消失了。

## eza —— 更好看的 ls

`eza`（`exa` 的活跃继任者）给 `ls` 加上颜色、图标、树状视图和 Git 状态：

```bash
eza -l --git              # 长列表 + 显示每个文件的 git 状态
eza --tree --level=2      # 像 tree 一样展示两层目录
eza -la --icons           # 带图标的全部文件列表
```

## tldr —— 看人话版的 man

`man` 太长太啰嗦时，用 `tldr` 看几个最常用的例子：

```bash
tldr tar                  # 直接给出 tar 解压/压缩的常用命令示例
tldr git rebase
```

对「我就想知道这命令怎么用」的场景特别合适。

## 把它们焊进 shell

光装上还不够，配几行才顺手。以 zsh 为例，在 `~/.zshrc` 里：

```bash
# 用现代工具替换默认命令
alias ls='eza --icons'
alias cat='bat'
alias find='fd'

# 初始化 zoxide（提供 z / zi）
eval "$(zoxide init zsh)"

# fzf 的按键绑定与补全
source <(fzf --zsh)
```

再配上 zsh 的自动补全和 `zsh-autosuggestions`（根据历史给灰色补全建议）、`zsh-syntax-highlighting`（命令实时高亮），日常敲命令的体验会上一个台阶。

## 小结

| 老命令 | 现代替代 | 一句话 |
| --- | --- | --- |
| `grep` | `rg`（ripgrep） | 更快、默认递归、尊重 .gitignore |
| `find` | `fd` | 语法简单、带颜色 |
| `cat` | `bat` | 语法高亮 + 行号 |
| `cd` | `zoxide`（`z`） | 记住常用目录、关键词跳转 |
| `ls` | `eza` | 颜色、图标、Git 状态、树视图 |
| `man` | `tldr` | 只看常用例子 |
| 历史/筛选 | `fzf` | 万能模糊查找，`Ctrl+R` 必装 |

不用一次全上，挑一两个最戳痛点的先用起来——大概率从 `rg` 和 `fzf` 开始就回不去了。
