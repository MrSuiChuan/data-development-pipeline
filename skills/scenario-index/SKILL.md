---
name: scenario-index
description: 场景路由：按工单内容选择互斥叠加场景（cdm-dev / feature-dev / metric-dev），决定 P6 是否必进语义层
---

# scenario-index

/start 进入 P2 前执行一次路由。读 `requirement_spec.md`，判定叠加场景（互斥，取最高匹配）：

- **cdm-dev（中间层）**：产出 dwd/dws 公共层、被多下游复用 → 叠加 cdm-dev；
- **feature-dev（特征标签）**：产出特征/标签表供算法/画像 → 叠加 feature-dev；
- **metric-dev（指标报表）**：产出指标/报表口径或语义层模型 → 叠加 metric-dev，**P6 必进语义层**；
- 都不命中 → 无叠加，P6 由建议书决定。

输出：`{scenario: "cdm-dev"|"feature-dev"|"metric-dev"|null, reason}`。
选定后加载对应 SKILL，其余场景 skill 不加载。
