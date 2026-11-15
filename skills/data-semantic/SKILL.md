---
name: data-semantic
description: P6 语义层方法论：先出建议书，用户拍板后才委托 dp-semantic-model / kb-publish 发布
---

# data-semantic（P6）

## 铁律

**AI 不得自行决定进入语义层更新**。流程：

1. 产出建议书：是否更新 + 判断逻辑 + 拟更新项（模型/指标/维度/YAML）；
2. 锁写 confirmation_gate；
3. AskUserQuestion 请用户拍板（autopilot 不旁路）；
4. 拍板后：把结果写入 `_user_confirmation.md`（含 `[已确认]`），再执行引擎
   `semantic` 命令（无确认记录被 semantic_entry_gate 拦截）；
   dp-semantic-model skill 查表元数据 → 生成 5 类 YAML → dpsl.py 校验 →
   委托 knowledge-base 插件 kb-publish（git push + OV 同步）；
5. 写 `semantic_update_summary.md`（>=10 行 + 模型信息）→ finish。

`finish` 会自动调用 knowledge-base-plugin 的 `ingest`，把本单已确认的 SDS、DDL、
测试和交付产物写回知识库。P6 的用户拍板只决定语义层摘要是否生成，不替代这次自动回写；
知识库写入失败只会落 `knowledge_update_result.json`，不会回滚已经完成的开发任务。

OSI 类型 commit 才允许 `--skip-audit` 逃生口；普通 commit 必须过 audit_completion_guard。
注意：knowledge-base 是独立插件，它侧门控失效时本插件无兜底——验收时人工抽查 OV 可见性。
