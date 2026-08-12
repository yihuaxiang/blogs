---
title: 别把 CSV 当作文本：构建可校验、可恢复的数据导入流水线
date: 2026-08-13 03:05:31
tags:
  - CSV
  - 数据导入
  - 数据质量
  - 后端
categories:
  - 工程实践
---

![CSV 数据导入流水线](/images/csv-import-pipeline/cover.jpeg)

CSV 常承载批量创建客户、更新库存和迁移历史记录等高风险动作。难点不在解析一行文本，而在表头被改名、日期格式混杂、同一文件重复上传，以及第 8 000 行失败后如何处理。把导入做成有边界、有记录的流水线，错误才可解释、结果才可追溯。

<!-- more -->

## 先定义导入契约，而不是猜列名

不要把第一行直接映射到数据库字段。每种导入任务都应有版本化模板契约：必填列、允许别名、数据类型、范围和业务约束。解析器只负责把字节变成单元格；契约层决定它们能否成为业务数据。

| 检查层次 | 示例 | 失败后的处理 |
| --- | --- | --- |
| 文件级 | UTF-8、文件大小、列数 | 拒绝整次导入 |
| 表头级 | `email`、`quantity` 是否存在 | 返回模板错误 |
| 行级 | 邮箱格式、数量为正整数 | 标注具体行 |
| 跨行级 | 邮箱是否重复、总量是否超限 | 给出汇总错误 |
| 业务级 | 商品是否存在、状态是否允许更新 | 暂存为不可执行 |

列顺序不应成为契约。规范化表头并维护别名可兼容旧模板，但不要过度宽容：未知列应提示，关键字段必须精确匹配。日期、金额和地区等有歧义的值，模板应指定格式与时区，不能依赖机器的本地设置。

```ts
type Row = { email: string; quantity: number; sku: string };

function validateRow(raw: Record<string, string>, line: number): Row {
  const email = raw.email?.trim().toLowerCase();
  const quantity = Number(raw.quantity);
  if (!email?.includes("@")) throw new RowError(line, "email 格式无效");
  if (!Number.isSafeInteger(quantity) || quantity <= 0) {
    throw new RowError(line, "quantity 必须是正整数");
  }
  if (!raw.sku?.trim()) throw new RowError(line, "sku 不能为空");
  return { email, quantity, sku: raw.sku.trim() };
}
```

## 解析、校验、执行分成三个阶段

![CSV 行校验与隔离](/images/csv-import-pipeline/validation.jpeg)

把“读 CSV”和“写数据库”放在一个循环，最容易半成功：前几百行已落库，后面报错，操作者无法放心重传。可靠流程应先创建 `import_id`，保存不可变原文件；随后流式解析至暂存表，记录行号、原始值、规范化值和错误；只有校验通过的批次进入执行。

暂存表可在不触碰正式数据前展示预览，例如“共 10 240 行、通过 10 198 行、42 行有错误”。错误应包含行号、列名、收到的值和修复建议；日志附加 `import_id`。大文件应逐行或分块读取，不能一次性载入内存。

```sql
CREATE TABLE import_rows (
  import_id UUID NOT NULL,
  line_no INTEGER NOT NULL,
  normalized JSONB,
  errors JSONB NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  PRIMARY KEY (import_id, line_no)
);
```

执行时以小批次事务提交，例如每 200 行一批；但“同一业务记录只产生一次效果”仍须由唯一约束或幂等键保证。批次大小是性能参数，不是正确性策略：重试不能重复扣库存、发通知或覆盖后来更新的数据。

## 将重复提交和并发纳入设计

用户遇到超时会再次上传，定时任务也可能重放同一文件。为原文件计算 SHA-256，并保存操作者、模板版本和摘要；同一范围内相同摘要的执行中任务直接返回已有 `import_id`。这能避免网络重试制造两次导入，但哈希不是唯一业务判断：不同文件也可能针对同一客户集合，仍要用业务键和版本条件约束。

更新现有记录时，每行应携带稳定标识和预期版本。条件写入发现版本不一致，就标为冲突，不能让旧 CSV 静默覆盖新修改。是否要求全成功取决于业务：价格表通常全有或全无；补录客户标签可让有效行先执行，但要保留失败报告。

## 给失败留下可恢复的出口

![可恢复的导入错误处理](/images/csv-import-pipeline/recovery.jpeg)

导入任务应有 `uploaded`、`validating`、`ready`、`running`、`completed`、`failed` 等状态。状态变更、执行者和错误摘要要落库，不能只依赖队列日志。进程崩溃后，租约过期的任务可安全重领；短暂故障可退避重试；模板错误和字段冲突则停止重试，等待修复。

上线前检查：模板是否可下载且有版本号；写正式表前能否看到完整错误；重传是否会产生第二次副作用；能否按 `import_id` 查到来源、进度和结果；失败报告能否指导修复。把 CSV 导入当作小型数据产品，用户交付的就不再是不可控文本，而是一条可验证、执行和恢复的业务变更。
