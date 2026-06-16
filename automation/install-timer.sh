#!/usr/bin/env bash
# 安装 / 更新「每天凌晨 3 点自动写博客」的 systemd user timer。
# 幂等：可重复执行。需要先准备好密钥文件（见下方提示）。

set -euo pipefail

REPO_DIR="/home/fudongdong/blogs"
UNIT_SRC="$REPO_DIR/automation/systemd"
UNIT_DST="$HOME/.config/systemd/user"
ENV_FILE="$HOME/.config/blog-autopost/env"

echo ">> 准备目录"
mkdir -p "$UNIT_DST" "$(dirname "$ENV_FILE")"

echo ">> 拷贝 systemd 单元"
cp "$UNIT_SRC/blog-autopost.service" "$UNIT_DST/"
cp "$UNIT_SRC/blog-autopost.timer"   "$UNIT_DST/"

echo ">> 可执行权限"
chmod +x "$REPO_DIR/automation/auto-write-post.sh"

if [ ! -f "$ENV_FILE" ]; then
  echo ">> 创建密钥文件模板：$ENV_FILE（请填入真实 MINIMAX_API_KEY 后再启用）"
  umask 077
  cat > "$ENV_FILE" <<'EOF'
# blog-autopost 运行所需环境变量（此文件在仓库之外，不会被提交）
MINIMAX_API_KEY=
EOF
  chmod 600 "$ENV_FILE"
fi

echo ">> 重新加载 user 单元"
systemctl --user daemon-reload

echo ">> 启用并启动 timer"
systemctl --user enable --now blog-autopost.timer

echo ">> 开启 linger（让 timer 在未登录时也能于凌晨触发）"
if ! loginctl enable-linger "$USER" 2>/dev/null; then
  echo "   !! 无权限开启 linger，请手动执行： sudo loginctl enable-linger $USER"
fi

echo
echo ">> 当前 timer 状态："
systemctl --user list-timers blog-autopost.timer --all || true
echo
echo "完成。提示："
echo "  - 确认已在 $ENV_FILE 填入 MINIMAX_API_KEY"
echo "  - 手动测试一次： systemctl --user start blog-autopost.service"
echo "  - 看日志： ls -t ~/.local/share/blog-autopost/ | head; "
echo "            tail -f ~/.local/share/blog-autopost/run-*.log"
