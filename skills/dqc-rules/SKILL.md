---
name: dqc-rules
description: 数据质量规则设计：主键唯一/非空/行数波动/口径一致性，产出 P5 布防清单
---

# dqc-rules

为生产表设计 DQC 规则，输出结构化清单：

```json
{"table": "…", "rules": [{"name": "pk_unique", "params": {"column": "user_id"}, "severity": "error"}]}
```

默认规则模板：主键唯一（PK）、键字段非空、行数波动阈值（日环 <±50%）、口径抽样一致性、
分区延迟告警。L2/L3 要求部署；L1 存量改造至少配置核心 PK/非空规则。
规则要可执行可验证——“应该有质量监控”不是规则。
