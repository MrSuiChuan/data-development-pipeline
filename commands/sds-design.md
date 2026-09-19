---
description: Phase1 数据概要设计：八条硬规则 + 知识缺口复核门 + 增量沉淀门 + 确认门 + 终版贴单
argument-hint: <T-xxxx>
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion, Task]
---

# /sds-design <T-xxxx> — 数据概要设计（独立且最重的门）

P1 是唯一不在 `/start` 内的阶段。“想清楚”与“动手做”必须分离；SDS 结论要钉在
工单评论上，成为跨会话契约。**start 准入门以此终版为唯一锚点。**

## 步骤

1. 查工单：`py -3 .data-dev/engine/ddp.py ticket show --ticket <T-xxxx>`。
   然后进入 SDS（P1 唯一入口，写 `sds_design.json` 前必须先进入本阶段）：
   `py -3 .data-dev/engine/ddp.py sds enter --mode verify|generate --ticket <T-xxxx>`
   （工单声明了来源用 verify，没声明来源用 generate）。
   解析正文（含外部需求文档链接则递归读取）。判断模式：
   - 工单声明了来源 → `verify`；
   - 未声明来源 → `generate`（先知识库四路径穿透找候选再生成来源）。

2. 调度 `data-development-plugin:sds-designer` agent 做逐条口径判定：
   知识库分层/域口径 + SQL 五问验证。产出 `.data-dev/tickets/<T-xxxx>/sds_design.json`
   与 `verification/evidence.json`、`verification/q*.sql`。
   - 任何映射结论为 `undeterminable` → 不许进终版，转知识缺口；
   - `knowledge_source_uri` 必须以 `knowledge.uri_prefix`（默认 `viking://resources/`）开头（kb_scope_guard 硬拦）。

3. 校验八条规则：
   `py -3 .data-dev/engine/ddp.py sds validate --ticket <T-xxxx>`（缺 confirmed 不阻断）。
   - 若有 `HALTED`（evidence 比对不一致）→ 立即停下叫用户人工核查，不继续。
   - 有 FAIL 缺陷 → 修复并重跑。

4. **知识缺口复核门**（`knowledge_gaps` 非空时）：AskUserQuestion 让用户二选一：
   刷新该域知识库（半自动挂起）/ 开发供知识（主力）。然后
   `py -3 .data-dev/engine/ddp.py sds review --ticket <T-xxxx> --action refresh|supply`。
   `kb_round` 超 2 次自动 `blocked` 终止。

5. **增量沉淀门**（命中候选时）：AskUserQuestion：沉淀（走 domain-knowledge-generator →
   kb-publish，OV 可见后记 synced）/ 明确拒绝（declined）。决议：
   `py -3 .data-dev/engine/ddp.py sds backfill-resolve --ticket <T-xxxx> --action synced|declined --reason "…"`。

6. **确认门**：`py -3 .data-dev/engine/ddp.py sds render --ticket <T-xxxx>` 生成四段式说明书，
   用 AskUserQuestion 请用户确认（**不要自己确认**）。

7. 用户确认后：先把确认记录写入 `_user_confirmation.md`（含 `[已确认]`），再
   `py -3 .data-dev/engine/ddp.py sds validate --ticket <T-xxxx> --confirm`
   落 `confirmed=true`（无确认记录会被 sds_confirm_gate 拦截）→ `sds build-comment` 组装【数据概要设计 vN】评论
   （锚头 + 说明书 + json 机读块 + 确认行）→ 用引擎 `comment add`（内部调 comment）
   贴到工单 → `ticket verify-latest` 回读校验 → `sds publish --version N` 置 published。

8. 桥接 P2-P6 统一输入：`py -3 .data-dev/engine/ddp.py sds bridge --ticket <T-xxxx>`，
   生成 `requirement_spec.md` 并落 `level` 与 `ticket_hash`。

9. 汇报：难度建议、终版评论版本、requirement_spec.md 位置；下一步 `/start <T-xxxx>`。
   **不流转工单状态**（保持“已提交”，“开发中”留给 /start）。

> 记住：校验器不信任 AI 自述，八条规则每次重跑；贴单内容在 start 时会被再次重验。
