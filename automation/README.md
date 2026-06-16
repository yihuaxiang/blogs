# 博客自动写作定时任务

每天**凌晨 3 点**由 systemd user timer 触发 `codex`，自动写一篇**全新的、图文并茂**的技术文章并发布到 GitHub Pages。

## 组成

| 文件 | 作用 |
| --- | --- |
| `automation/auto-write-post.sh` | 入口脚本：加载环境/密钥 → 调 codex 写文章+配图 → `hexo generate` 校验 → 提交并 push |
| `automation/systemd/blog-autopost.service` | systemd oneshot 服务，执行上面的脚本 |
| `automation/systemd/blog-autopost.timer` | 每天 `03:00` 触发（`Persistent=true` 错过会补跑） |
| `automation/install-timer.sh` | 一键安装：拷贝单元、reload、enable、开 linger |
| `../AGENTS.md` | 给 codex 的写作规则（选新题材、图文并茂、图片路径不带 `/blogs`、构建自检、不自行 git 提交） |
| `../.codex/skills/minimax-image/` | 自包含的 MiniMax 配图脚本（图片生成） |

## 密钥（不进仓库）

`MINIMAX_API_KEY` 等放在仓库**之外**的 `~/.config/blog-autopost/env`（权限 600），由脚本与 systemd 读取：

```
MINIMAX_API_KEY=sk-...
```

> 切勿把密钥写进仓库内任何文件。

## 安装

```bash
# 1) 填好密钥
mkdir -p ~/.config/blog-autopost
printf 'MINIMAX_API_KEY=%s\n' '你的key' > ~/.config/blog-autopost/env
chmod 600 ~/.config/blog-autopost/env

# 2) 一键安装 timer
bash automation/install-timer.sh
```

## 常用命令

```bash
# 看下一次触发时间
systemctl --user list-timers blog-autopost.timer --all

# 立刻手动跑一次（测试）
systemctl --user start blog-autopost.service

# 看日志
tail -f ~/.local/share/blog-autopost/run-*.log

# 暂停 / 恢复
systemctl --user disable --now blog-autopost.timer
systemctl --user enable  --now blog-autopost.timer
```

## 注意

- codex 在本机以 `--dangerously-bypass-approvals-and-sandbox` 全自动运行（本仓库已是 codex 的 trusted 项目）；
  若该参数随 codex 版本变化，用 `codex exec --help` 核对后改 `auto-write-post.sh`。
- git push 走 SSH，需要本机 SSH key 可非交互使用（无 passphrase 或已在 agent 中）。
- 脚本带护栏：构建失败、或产物出现 `/blogs/blogs/` 双前缀时会放弃发布，不会推坏内容。
