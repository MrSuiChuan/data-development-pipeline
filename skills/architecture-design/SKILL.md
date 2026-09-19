---
name: architecture-design
description: P2 架构设计方法论：按难度分路径（L1 精简 / L2/L3 全景四层+方案对比+影响分析）
---

# architecture-design（P2）

/start 进入 P2 时加载。输入是 `requirement_spec.md`（SDS 桥接产物）。

## 按难度分流

- L1：精简路径——复核目标表 + 查下游 + 判加列/改值 + 影响范围；不画架构方案；
- L2/L3：四层架构（适配层→逻辑层→接口层→核对层）+ >=3 方案对比 + 影响分析（下游清单、
  回刷范围、DQC、语义层）。

## 交付物门控

- `architecture_doc.md`：必须含难度级别（与 state.level 一致）与数据链路图（mermaid 或图）；
- `pipeline.html`：L2/L3 必须有，且无占位符；
- 占位符（TODO/lorem）一旦存在，stage_gate 直接 FAIL。

## 交付表清单（必写，机器要读）

`architecture_doc.md` 里必须有一个 ```json 机读块，声明本单要交付的全部表：

````markdown
## 交付表清单

```json
{
  "tables": [
    {"name": "dwd_user_act_di", "action": "create", "layer": "dwd"},
    {"name": "ads_user_act_di", "action": "alter", "layer": "ads"}
  ]
}
```
````

- 只强制 `name`；`action` / `layer` 写不写都行（现在不校验，留作后续扩展）。
- **等值校验**：P3 出口要求 `ddl/` 里的表名集合与这份清单**完全相等**。
  多造一张（没设计过的表）或少造一张（设计里砍掉的表）都会 FAIL。
- 中途要增减表 → 必须回跳 P2 改这份清单再回 P3：
  `advance --allow-back --reason "架构变更：新增中间表 X"`。**不要在 P3 里偷偷改设计。**
- L1 通常只有一行；L2/L3 四层架构会把中间表一并列进来。

架构结论里出现的表、任务必须可追溯到 requirement_spec.md；新出现的依赖要标注来源。
