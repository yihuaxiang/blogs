---
title: 把 CLI 设计成可组合组件：标准流、退出码与机器可读输出
date: 2026-08-14 03:04:35
tags:
  - CLI
  - 工具设计
  - 自动化
  - 工程实践
categories:
  - 工程实践
---

![可组合 CLI 的数据流](/images/cli-composable-design/cover.jpeg)

命令行工具不只是给人敲的快捷入口，更是脚本、CI 与其他程序会调用的小型接口。只能打印漂亮文本的命令，第二次自动化时往往就得重写；输入、输出和失败语义清晰的 CLI，则能自然接进管道，成为可靠工作流的积木。

<!-- more -->

## 把三种信息分开放

业务结果写到标准输出（stdout），进度、警告和错误说明写到标准错误（stderr），成功或失败由退出码表达。下游程序因此只接到数据，人在终端仍能看见诊断信息。

| 通道 | 放什么 | 谁消费 |
| --- | --- | --- |
| stdout | 结果与结构化记录 | 管道、重定向、程序 |
| stderr | 进度、警告、错误说明 | 人与日志系统 |
| exit code | 完成状态和失败类别 | Shell、CI、编排器 |

例如，导出工具不应把“正在读取项目”混进 JSON：

```bash
projectctl export --format json > projects.json
```

进度仍显示在终端，文件里却始终是可解析的数据。Node.js 入口可以这样保持边界：

```js
try {
  const rows = await exportProjects(options);
  process.stdout.write(JSON.stringify(rows) + "\n");
} catch (error) {
  console.error(`projectctl: ${error.message}`);
  process.exitCode = 1;
}
```

不要在业务函数深处直接 `exit()`；由入口层统一报告错误，测试也能复用核心逻辑。

## 输入要同时适合人和管道

交互式默认值很方便，但不应代替明确参数。约定 `-` 表示从标准输入读取，允许重复的 `--id`，危险动作要求 `--yes`。来自文件或管道的记录应逐条校验，不能静默跳过坏数据。

![结构化记录经过命令管道](/images/cli-composable-design/data-flow.jpeg)

批量处理时，JSON Lines（每行一个 JSON 对象）通常比巨大 JSON 数组更合适：可以流式读取，坏记录能定位到行号，也不必把全部内容放入内存。

### 流式格式让恢复更简单

当某一行校验失败时，工具应在 stderr 报出行号与原因，并以非零状态结束；已经确认写入的结果则要带上可追踪的 ID。调用者可以据此修复源数据后从失败位置继续，避免把整批任务变成一次性的黑盒操作。

```bash
projectctl list --format jsonl \
  | projectctl validate --input - \
  | projectctl apply --input - --yes
```

面向人的表格可以是默认展示，但要提供显式的 `--format json` 或 `--format jsonl`。脚本不要解析列宽、颜色和标题；文案一改，`awk` 之类的处理就可能悄悄选错字段。

## 退出码少而稳定

Shell 只区分零和非零，调用者却常要知道能否重试。为少数常见类别建立稳定约定，文档化后不要随意改义。

| 退出码 | 语义 | 调用者动作 |
| --- | --- | --- |
| 0 | 成功 | 继续 |
| 2 | 参数或输入错误 | 修正后再运行 |
| 3 | 资源不存在 | 更换目标或停止 |
| 4 | 临时依赖失败 | 退避重试 |
| 1 | 未归类错误 | 保留现场排查 |

脚本应按状态决策，而不是搜索错误文案：

```bash
if projectctl sync --remote origin; then
  :
else
  code=$?
  if [[ $code -eq 4 ]]; then
    echo "remote unavailable; retry later" >&2
  fi
  exit "$code"
fi
```

这样临时失败的状态会完整传给外层重试器。不要写 `if ! cmd; then code=$?`：`!` 已经把状态取反，保存到的不是原退出码。

## 把可组合性当成契约

![CLI 契约中的成功与失败路径](/images/cli-composable-design/contract.jpeg)

错误应包含命令名、失败对象和下一步建议，但错误文本不是机器接口。自动化若需细节，应提供 JSON 中稳定的 `code`、`message` 与上下文字段。颜色、进度条和提问仅在连接 TTY 时启用，并提供 `--no-color`、`--quiet`、`--non-interactive`。

### 契约测试比快照文案更可靠

测试不必锁死每一段错误文案，但应锁定字段、通道和状态。例如把 stdout 交给 JSON 解析器，确认 stderr 只含诊断，再为“参数错误”和“依赖暂不可用”分别断言退出码。这样既允许优化提示，也不会破坏自动化调用者。

最后用测试守住边界：断言 stdout 可解析、stderr 不混入结果、退出码正确，并实际跑一次管道。把这些当作公开契约，CLI 就能长期成为可预测、可测试的组件，而不是只能手工使用的命令。
