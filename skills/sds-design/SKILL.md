---
name: sds-design
description: SDS 数据概要设计方法论：verify/generate 双模式、SQL 五问、八条硬规则、知识缺口/增量沉淀/确认三门
---

# sds-design（P1 方法论）

/sds-design 命令加载本 skill 后执行。P1 是唯一不在 /start 内的阶段，结论钉在工单评论上。

## 双模式

- 工单声明来源 → verify：判知识库分层/域口径是否与该声明一致 → accurate/inaccurate+correction/undeterminable；
- 无声明 → generate：知识库四路径穿透（域索引→memory→资产→同类工单）找候选 → 生成来源 → 同套五问验证。

## SQL 五问（每次验证必须有机器数字）

1. 源表存在吗？（row_count>0）2. 主键/键字段空值率？（键字段必须 0）3. 与目标口径 join
   match_rate？（PK>=0.995 / 维度>=0.95）4. 取值域与业务含义一致？5. 分层是否合规（明细↔汇总）？

## 八条硬规则（validate_sds_design 落地）

结构完备 / 结论完备 / 溯源 `knowledge.uri_prefix`（默认 `viking://resources/`）/ 分层 / 验证证据阈值 / evidence 比对
（abs_tol=1e-9，不一致=HALTED 人工核查）/ 知识缺口非空须 ≥3 轨迹且 1 条命中根 /
增量沉淀候选须 synced|declined。

## 三门纪律

- 复核门（knowledge_gap）：人工二选一，kb_round≤2，超限 blocked；
- 沉淀门：domain-knowledge-generator→kb-publish 或 declined，经 state 决议落盘；
- 确认门：渲染四段式说明书 → AskUserQuestion → confirmed=true。

质量真防线是 validator + 抽样重放，不指望人逐字段核对；你也不许自封 confirmed。
