---
title: 把 Shell 脚本写稳：失败处理、临时目录与清理清单
date: 2026-07-06 03:07:50
tags:
  - Shell
  - Bash
  - 自动化
  - 工程实践
categories:
  - 工程实践
---

![可靠 Shell 脚本](/images/reliable-shell-scripts/cover.jpeg)

Shell 脚本常常从一两行命令开始，后来慢慢变成构建、发布、备份、迁移和运维入口。它的问题也在这里：看起来只是“把命令串起来”，一旦变量为空、管道中间失败、临时文件没清理，影响就会被放大。写稳 Shell 脚本，不是把 Bash 语法背全，而是给失败路径、输入边界和资源清理留出明确位置。

<!-- more -->

## 什么时候需要认真对待脚本

如果脚本只在本机临时跑一次，写得短一点问题不大。但下面几类脚本应当按工程代码处理：会进入 CI 的脚本、会删除或覆盖文件的脚本、会调用外部服务的脚本、会被多人复用的脚本，以及需要在不同目录或不同机器上执行的脚本。它们共同的风险是“默认成功”的假设太多。

| 风险 | 常见症状 | 稳定做法 |
| --- | --- | --- |
| 变量为空 | `rm -rf "$DIR"` 中 `DIR` 没赋值 | 打开未定义变量检查，先验证参数 |
| 管道失败被吞 | `curl | jq` 只看到最后一步结果 | 使用 `pipefail`，必要时拆开检查 |
| 临时文件残留 | 失败后留下半成品目录 | `mktemp -d` 配合 `trap` 清理 |
| 路径带空格 | 循环或拼接后文件找不到 | 始终引用变量，数组保存参数 |
| 日志太少 | CI 只显示最后一行失败 | 统一日志函数，错误信息带上下文 |

## 从脚本骨架开始

![脚本执行流程](/images/reliable-shell-scripts/pipeline.jpeg)

可靠脚本的第一步，是让默认行为更保守。下面这个骨架适合大多数 Bash 自动化入口：

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

trap 'die "failed at line $LINENO: $BASH_COMMAND"' ERR
```

`set -e` 让命令失败时尽快退出，`-u` 把未定义变量当成错误，`pipefail` 让管道中任一步失败都能反映到整体状态。`-E` 则让 `ERR` trap 在函数和子 shell 场景里更一致。它们不是银弹：有些命令失败是业务预期，例如检查文件是否存在，就要显式写进条件分支。

```bash
if [[ ! -f "$config" ]]; then
  die "config file not found: $config"
fi

if grep -q 'debug=true' "$config"; then
  log "debug mode enabled"
fi
```

关键是把“允许失败”和“不能失败”区分开，而不是在脚本末尾才发现状态不对。

## 参数先校验，再执行动作

很多事故来自脚本入口太随意。参数不要边用边猜，先集中解析和校验。对复杂命令可以用 `getopts`，对少量参数则保持直白：

```bash
usage() {
  cat >&2 <<'EOF'
Usage: ./release.sh --env <staging|prod> --version <tag>
EOF
}

env_name=""
version=""

while (($#)); do
  case "$1" in
    --env) env_name="${2:-}"; shift 2 ;;
    --version) version="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "unknown argument: $1" ;;
  esac
done

[[ "$env_name" =~ ^(staging|prod)$ ]] || die "invalid env: $env_name"
[[ -n "$version" ]] || die "missing --version"
```

这里的重点不是写出最短代码，而是让错误在入口处停止。脚本越靠近发布、迁移和清理任务，越应该早失败、说清楚、不要猜。

## 临时目录和清理要成对出现

![临时目录清理](/images/reliable-shell-scripts/cleanup.jpeg)

需要中间产物时，优先用 `mktemp -d` 创建独立目录，再用 `trap` 保证退出时清理。不要手写固定路径，例如 `/tmp/build`，否则并发执行、权限残留和旧文件污染都会变成隐患。

```bash
tmpdir="$(mktemp -d)"

cleanup() {
  rm -rf "$tmpdir"
}

trap cleanup EXIT

archive="$tmpdir/app.tar.gz"
tar -czf "$archive" dist/
upload_artifact "$archive"
```

如果脚本需要保留现场排障，可以加一个开关，而不是临时注释清理逻辑：

```bash
KEEP_TMP="${KEEP_TMP:-0}"

cleanup() {
  if [[ "$KEEP_TMP" == "1" ]]; then
    log "keep temp directory: $tmpdir"
  else
    rm -rf "$tmpdir"
  fi
}
```

这样 CI 默认干净，本地排障也有明确入口。

## 引用变量，用数组传参数

Shell 最容易误伤人的地方，是单词拆分和通配符展开。规则可以简单记：变量展开默认加双引号，多个参数用数组保存。

```bash
files=("$@")

for file in "${files[@]}"; do
  [[ -f "$file" ]] || die "not a file: $file"
  cp -- "$file" "$tmpdir/"
done
```

`--` 用来告诉命令“后面都是参数值，不再解析选项”，可以避免文件名以 `-` 开头时被误判。数组则能保留每个参数的边界，不会因为空格、换行或通配符把一个文件拆成多个片段。

## 给脚本留测试入口

Shell 脚本也应该能被检查。最小成本是把核心逻辑拆成函数，让主流程放在文件底部；然后在 CI 里跑语法检查、静态检查和一两个干跑场景。

```bash
main() {
  parse_args "$@"
  build_package
  publish_package
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
```

常用检查可以放进 Makefile 或 CI：

```bash
bash -n scripts/*.sh
shellcheck scripts/*.sh
DRY_RUN=1 scripts/release.sh --env staging --version test
```

`DRY_RUN` 不必覆盖所有细节，但应能验证参数解析、路径拼接、日志输出和危险动作是否被拦住。

## 一份落地清单

给现有脚本补强，可以按这个顺序推进：先加 `set -Eeuo pipefail` 和统一错误日志；再把入口参数集中校验；随后把临时文件迁到 `mktemp -d` 并用 `trap` 清理；接着修正未加引号的变量和容易拆词的循环；最后把 `bash -n`、`shellcheck` 和一个干跑用例接到 CI。这样改动不大，却能覆盖 Shell 脚本最常见、也最难从日志里定位的失败模式。
