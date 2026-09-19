---
name: session-review
description: 会话收尾复盘：核对门控日志、证据链与未决项，输出旁路评测摘要
---

# session-review

阶段收尾或用户要求复盘时加载。检查：

1. 先跑 `ddp.py report --ticket <id> --write` 拿治理摘要（时间线+证据核验+决策），
   再核对 `_state.json.transition_log` 有没有“跳门/绕过”痕迹（应只有 advance/allow-back 记录）；
2. 各阶段机器证据是否齐全（etl_dev_exec / test_runs / deploy_log / semantic summary）；
3. autopilot_decisions.md 的 ⚠ 行是否都对应 AskUserQuestion；
4. 输出一份复盘摘要：已完成/未决/风险点/下一步。只做只读评估，不修改状态。
