---
title: Git 冲突反复解别再重做：用 rerere 记住正确答案
date: 2026-09-21 03:00:37
tags:
  - Git
  - 版本控制
  - 冲突解决
  - 工程效率
categories:
  - 工程实践
---

![Git rerere 复用冲突解决结果](/images/git-rerere-conflict-resolution/cover.jpeg)

长分支反复 rebase、发布分支持续回合并、补丁在多个版本间 cherry-pick 时，同一处冲突可能一再出现。人工每次都重新判断，不仅浪费时间，还可能给出前后不一致的答案。Git 内置的 `rerere`（reuse recorded resolution）能记录一次冲突的解决前后状态，在相同冲突再次出现时复用结果。

<!-- more -->

## rerere 记住的不是整个文件

遇到内容冲突后，Git 会在工作区留下冲突标记。启用 `rerere` 时，它先把冲突片段的规范化形态记为 **preimage**；当你编辑完成并执行 `git add` 后，再把解决后的形态记为 **postimage**。以后识别到相同的冲突形态，Git 会把对应结果应用到工作区。

这不是按文件路径保存一份最终文件，也不是自动判断哪一边正确。它复用的是你已经确认过的局部解决方案，因此文件其他位置的新改动仍会保留；若上下文变化太大、无法匹配，Git 仍会要求人工处理。

| 阶段 | rerere 的动作 | 开发者要做什么 |
| --- | --- | --- |
| 首次冲突 | 记录冲突前形态 | 理解两侧意图并正确合并 |
| 标记解决 | 记录解决后形态 | 测试并执行 `git add` |
| 冲突重现 | 尝试套用历史解法 | 审查差异、测试、再暂存 |

## 先采用保守配置

可以只在当前仓库启用，也可以全局启用：

```bash
# 当前仓库
git config rerere.enabled true

# 熟悉流程后再考虑全局开启
git config --global rerere.enabled true
```

建议一开始不要开启 `rerere.autoupdate`。默认情况下，匹配到的解法会写入工作区，但不会替你暂存，审查关口仍然存在。若开启自动暂存，复用成功后索引也会更新，速度更快，却更容易让人忘记检查语义：

```bash
git config rerere.autoupdate false
```

![冲突解法从记录到复用的循环](/images/git-rerere-conflict-resolution/resolution-cycle.jpeg)

## 完整走一遍记录与复用

假设 `main` 与 `feature` 都修改了同一段配置。第一次合并产生冲突后，先确认状态：

```bash
git merge feature
git status --short
git rerere status
```

编辑冲突文件，删除标记并保留业务上正确的组合，然后运行测试。只有确认结果正确，才把它交给 rerere：

```bash
git diff
npm test
git add config/app.json
git commit
```

在 rebase 或 cherry-pick 中再次遇到同形冲突时，终端通常会提示已解析。此时不要直接执行 `--continue`，先检查 Git 实际改了什么：

```bash
git status
git diff
git rerere diff
npm test
git add config/app.json
git rebase --continue
```

`git rerere diff` 用于查看记录的解法对当前冲突做了哪些修改。若还有未解决项，`git rerere remaining` 可以列出它们。把“自动复用”视为生成候选补丁，而不是正确性证明，尤其要关注配置默认值、权限判断和数据库迁移等高风险代码。

## 录错答案时如何撤回

如果第一次解决就是错的，继续复用只会放大错误。对当前冲突执行下面的命令，可忘记相关记录并恢复成待解决状态：

```bash
git rerere forget path/to/conflicted-file
```

然后重新编辑、测试并 `git add`，Git 会记录新的解决结果。仓库中的缓存位于 `.git/rr-cache/`，属于本地元数据，不会随普通提交分享给团队；不要把删除整个缓存目录当作日常纠错手段，因为那会丢掉其他仍有价值的记录。长期不用的条目可交给内置清理命令处理：

```bash
git rerere gc
```

## 哪些场景值得开启

`rerere` 最适合冲突模式会重复出现的工作流，例如长时间维护的功能分支、多个发布分支之间的补丁移植，以及为了验证历史而反复执行的 rebase。一次性、小规模合并得到的收益有限；二进制文件、整文件重写或语义已经改变的代码，也不应期待它可靠复用。

真正稳妥的流程可以概括为：首次认真解决，复用时审查差异，暂存前运行测试，发现错误立即 `forget`。`rerere` 消除的是重复劳动，而不是冲突背后的设计判断。把它放在测试与代码审查之间，才能既享受自动化，又守住合并质量。
