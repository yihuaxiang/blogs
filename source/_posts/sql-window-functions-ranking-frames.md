---
title: SQL 窗口函数别算错：分组排名、累计值与窗口边界
date: 2026-09-07 03:04:35
tags:
  - SQL
  - SQLite
  - 窗口函数
  - 数据分析
categories:
  - 工程实践
---

![保留明细行并通过窗口计算分组统计](/images/sql-window-functions-ranking-frames/cover.jpeg)

报表既要展示每笔销售，又要附上门店排名和累计金额。用分组聚合会丢失明细，拉回应用层计算又增加数据传输。SQL 窗口函数能直接为每行补上统计结果，但排序并列、默认窗口和过滤位置都会改变答案。把这些规则写清楚，查询才经得起真实数据的检验。

<!-- more -->

## 一、先分清分区、顺序与窗口帧

普通 `GROUP BY shop` 聚合把每家门店压成一行；`SUM(amount) OVER (...)` 则保留参与窗口计算的明细行，为每行增加一个结果。理解 `OVER` 时，把三个问题分别回答：

| 子句 | 负责什么 | 示例含义 |
| --- | --- | --- |
| `PARTITION BY shop` | 划分独立计算的分区 | 每家门店重新开始 |
| `ORDER BY day, id` | 指定分区内的计算顺序 | 同一天再按唯一编号排序 |
| `ROWS BETWEEN ...` | 限定聚合读取的窗口帧 | 从分区起点累计到当前行 |

分区是大范围，窗口帧是当前行实际参与聚合的范围，两者不能混用。排名函数有自己的规则，不能靠缩小帧来改变排名。具体语义可查 [SQLite 窗口函数文档](https://www.sqlite.org/windowfunctions.html)。

## 二、每组取前两条，先约定如何处理并列

下面的样本可直接在 SQLite 中执行，金额使用整数单位：

```sql
CREATE TABLE sales (
  id INTEGER PRIMARY KEY,
  shop TEXT NOT NULL,
  day TEXT NOT NULL,
  amount INTEGER NOT NULL
);
INSERT INTO sales VALUES
  (1, 'A', '2026-09-01', 10),
  (2, 'A', '2026-09-01', 20),
  (3, 'A', '2026-09-02', 5),
  (4, 'B', '2026-09-01', 7),
  (5, 'B', '2026-09-02', 7);
```

![各个门店分区独立排名的概念插画](/images/sql-window-functions-ranking-frames/partition-ranking.jpeg)

### 精确取两条：使用 ROW_NUMBER

```sql
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY shop
    ORDER BY amount DESC, id
  ) AS rn
  FROM sales
)
SELECT shop, id, amount, rn
FROM ranked
WHERE rn <= 2
ORDER BY shop, rn;
```

A 店返回编号 `2、1`，B 店返回 `4、5`。唯一编号让同金额记录的顺序确定；缺少它，并列行的编号分配没有保证。某组不足两条时，只返回已有记录。

### 保留并列：使用 RANK 或 DENSE_RANK

若只按金额降序，金额为 `20、20、10` 时，`RANK` 给出 `1、1、3`，`DENSE_RANK` 给出 `1、1、2`。前者保留名次空缺，后者表示连续的金额档位。保留并列时，不要把唯一编号也放进排名窗口的排序，否则并列会被拆开；筛选前两名也可能返回超过两条记录。

## 三、累计金额要把窗口帧写出来

```sql
SELECT shop, id,
  SUM(amount) OVER (
    PARTITION BY shop
    ORDER BY day, id
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  ) AS running_amount
FROM sales
ORDER BY shop, day, id;
```

A 店按编号 `1、2、3` 得到 `10、30、35`。`ROWS` 按行确定边界，唯一排序保证逐笔累计的顺序稳定。

如果改成只按 `day` 排序，并省略帧定义，SQLite 默认的 `RANGE` 帧会把与当前行排序值相同的记录一起计入。因此 A 店前两笔虽然是不同记录，累计值却都为 `30`，第三笔为 `35`。这是默认帧的语义，并非重复计算。

![沿有序记录移动并限定计算范围的窗口概念插画](/images/sql-window-functions-ranking-frames/window-frames.jpeg)

把帧改成 `ROWS BETWEEN 2 PRECEDING AND CURRENT ROW`，就能统计当前及前两行；分区开头不足三行时按已有行计算。它表示“最近三笔”，并不表示“最近三天”。按自然日滚动统计时，需要先明确缺失日期是否补零，再选择目标数据库支持的时间范围方案。

## 四、过滤位置决定参与统计的数据

窗口函数看到的是经过同层 `WHERE` 过滤后的数据。想显示九月二日的销售，同时保留此前累计值，应先在内层计算累计，再在外层筛选日期；把日期条件提前放入内层，累计就只剩筛选后的记录。上面的排名查询同样先计算，再在外层按名次筛选。计算阶段的说明见 [PostgreSQL 窗口函数教程](https://www.postgresql.org/docs/current/tutorial-window.html)。

最后，窗口中的排序只决定计算顺序，页面展示顺序仍要用最外层 `ORDER BY` 保证。验证查询时，至少覆盖同一天多笔、金额并列、单行分区和跨分区累计；同时检查返回行数与明确列出的预期值，避免只看总金额恰好相等。
