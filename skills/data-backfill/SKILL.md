---
name: data-backfill
description: 历史回刷方法论：三阶段顺序、禁止 SUPPLEMENT、先读后写、range 受控
---

# data-backfill

回刷是生产写操作，遵守：

- 顺序：上游依赖 → 本表 → 下游核对；
- 类型：只做任务定义的覆盖式回刷，**禁止 SUPPLEMENT 类型**；
- 前置：先只读核对目标表结构与分区范围（backfill_guard，2h TTL 读缓存）；
- 范围：range_start<=range_end，日期受控，逐段小步回刷更安全；
- 证据：每次返回 run_id/status，失败如实报，不许“补完了”而无 run_id。

回刷动作归属 P5 部署门（AskUserQuestion），autopilot 不旁路。
