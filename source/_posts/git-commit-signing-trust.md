---
title: 用 Git 提交签名建立可信变更链：从本地密钥到 CI 验证
date: 2026-09-28 03:04:52
tags:
  - Git
  - 安全
  - DevOps
categories:
  - 工程实践
---

![Git 提交签名与仓库信任链](/images/git-commit-signing-trust/cover.jpeg)

代码评审能说明“谁看过改动”，却不能单独证明“这个提交确实由某个身份创建”。当仓库允许机器人合并、镜像同步或多人代提交时，提交者姓名和邮箱只是可编辑的文本。给提交加上数字签名，再让本地和 CI 按规则验证，才能把变更和密钥身份连起来。本文用 Git 原生能力搭一条可操作的信任链。

<!-- more -->

## 1. 先分清三种身份

Git 提交里常见的 `Author` 是最初写代码的人，`Committer` 是创建当前提交对象的人；两者都能被命令行参数修改。签名证明的是“持有私钥的人为这个提交签名”，它不自动等于公司员工或某个 GitHub 账号。因此，可信流程需要把公钥指纹、工作身份和仓库权限放在同一份登记表里，定期复核。

签名还保护了提交对象本身。提交的父提交、树对象、作者信息和提交说明都会参与哈希，任何字段被改动，原签名就无法通过验证。它不能替代代码审查，也不能证明代码没有漏洞，但能让审计者确认变更历史没有被静默替换。

![签名从提交到仓库的验证流程](/images/git-commit-signing-trust/signing-flow.jpeg)

## 2. 选择签名格式并做最小配置

Git 常见的两条路线是 GPG 和 SSH。已有 GPG 基础设施时可以继续使用；团队若已经用 SSH 管理开发机，SSH 签名通常更容易理解。下面以 SSH 为例，密钥只用于签名，不把私钥复制到服务器：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_git_signing
# 公钥登记到团队的 allowed signers 文件后，再配置 Git
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519_git_signing.pub
git config --global commit.gpgsign true
```

提交时 Git 会调用本机的 SSH agent 或私钥文件。先用一次小提交检查配置：

```bash
git commit -m "docs: explain signing policy"
git log --show-signature -1
git verify-commit HEAD
```

不要把私钥放进仓库、容器镜像或 CI 变量日志。开发机启用 agent，设置锁屏和密钥口令；CI 只负责验证公钥，不负责替开发者保存私钥。

## 3. 把“能验证”变成“按规则信任”

`git verify-commit` 能告诉你签名是否有效，但“有效”不等于“允许合并”。可以维护一个受审查的 `allowed_signers` 文件，每行记录邮箱、有效期和公钥：

```text
alice@example.com namespaces="git" valid-after=2025-01-01 ssh-ed25519 AAAA...
bot@example.com namespaces="git" valid-after=2025-01-01 ssh-ed25519 AAAA...
```

再让 Git 使用它验证身份：

```bash
git config --global gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
```

仓库策略应明确哪些分支、哪些提交类型必须签名。保护分支可以在 CI 中检查待合并提交：

```bash
for commit in $(git rev-list origin/main..HEAD); do
  git verify-commit "$commit" || exit 1
done
```

检查失败时要输出提交哈希和原因，方便作者修复；不要只返回一个模糊的“permission denied”。同时保留例外流程，例如紧急修复由值班负责人批准并记录工单，避免开发者为了绕过闸口而关闭验证。

## 4. 密钥轮换和撤销要提前设计

私钥丢失、设备退役或成员离职时，最危险的动作是只在聊天里通知“以后别用了”。应把公钥状态做成表格，并记录生效时间、失效时间、负责人和替代密钥。轮换步骤可以按下面的顺序执行：

| 步骤 | 操作 | 目的 |
| --- | --- | --- |
| 1 | 生成新密钥并登记指纹 | 让新身份可验证 |
| 2 | 在过渡期同时接受新旧公钥 | 避免中断发布 |
| 3 | 更新 CI、文档和开发机 | 消除隐式依赖 |
| 4 | 为旧公钥设置截止时间并撤销 | 阻止继续签名 |
| 5 | 抽查历史提交和审计日志 | 确认轮换没有误伤 |

![密钥轮换与 CI 闸口](/images/git-commit-signing-trust/key-rotation.jpeg)

历史提交通常不必重写。重写会改变哈希并破坏下游引用；更稳妥的做法是保留旧签名，标记密钥已撤销，再从撤销时间点开始阻止新提交。若怀疑私钥泄露，还要尽快暂停相关发布凭据，并检查泄露窗口内的提交和构建产物。

## 5. 一份能落地的检查清单

* 新成员入组时登记公钥指纹和用途，离组时撤销。
* 保护分支要求提交签名，合并机器人使用独立密钥。
* CI 验证完整提交范围，而不是只验证最新一个提交。
* 构建日志记录签名状态和策略版本，但不打印私钥或口令。
* 每季度演练一次密钥轮换和失效密钥恢复。

提交签名的价值不在于界面上的绿色徽章，而在于让每个变更都有可验证的来源、清晰的责任人和可执行的撤销路径。先从保护主分支和发布分支开始，再逐步覆盖普通分支，团队会更容易在安全性和开发速度之间取得平衡。
