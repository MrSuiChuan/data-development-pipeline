# data-development-plugin 架构文档

## 1. 运行时布局

`/setup` 之后，项目里出现 `.data-dev/`：

```
.data-dev/
  config.json                    # mode(mock|live)、CLI 名、阈值（kb_round_max、test_count_min…）
  current.json                   # 当前工单
  engine/                        # /setup 同步的引擎副本（ddp.py + core/）
  tickets/<T-xxxx>/
    _state.json                  # 机器状态（唯一真相源）
    _context.md                  # 状态镜像（机器写，勿手改）
    requirement_spec.md          # P2-P6 统一输入（上半工单原文 / 下半 SDS 桥接）
    sds_design.json  verification/  sds_design_spec.md
    architecture_doc.md  pipeline.html
    ddl/  etl/  dp_task/  etl_dev_exec.json
    test_report.md  test_runs.json
    deploy_plan.json  code_diff.html  review_report.html
    delivery_summary.md  deploy_log.json
    semantic_update_summary.md  autopilot_decisions.md
  mock/                          # mock 模式：tracker 工单/评论、platform 任务、kb 索引、线上基准
  .runtime/                      # hook 账本：重试熔断、读取缓存(TTL)
  .cli-output/                   # live CLI 大输出落盘
```

## 2. `_state.json` 契约

```json
{
  "ticket": "T-0001",
  "ticket_hash": "<工单正文 sha256>",
  "current_phase": 0..7,          // 0 已提交 / 1 SDS / 2..6 P2..P6 / 7 完成
  "finished": false,
  "run_mode": "interactive|autopilot",
  "level": "L0|L1|L2|L3",
  "sds_design": {
    "status": "none|in_progress|review_pending|review_backfill|pending_confirm|published|blocked",
    "mode": "verify|generate", "confirmed": false,
    "ticket_hash": "", "kb_round": 0, "backfill_resolved": null,
    "final_version": 0, "published_at": null, "halted": false
  },
  "completed": [1, 2, ...],       // 前向推进通过出口门后追加
  "awaiting_confirmation": false,
  "confirmation_scope": null,
  "autopilot_decisions": [],
  "transition_log": [],
  "context": { "verify_result": "passed", "self_check_result": "passed", ... }
}
```

### 转场规则（state.advance）

- 前向只允许 `target == current + 1`，且 `stage_gate(current)` 无 FAIL/HALTED；
- 回跳必须 `--allow-back --reason`：跳过前向门控、不追加 completed（copilot 穿插）；
- 完成后（`finished=true`）拒绝任何转场；
- 每次转场写 `transition_log`。状态只认机器证据。

## 3. 阶段出口门（stage_gate → validators）

### P1 SDS 八条硬规则（validate_sds_design）

1. **结构完备**：target_table 必填键、field_mappings 必填键、needs_intermediate_table 显式 bool；
2. **结论完备**：每条映射有结论；inaccurate 必填 correction；undeterminable 不许进终版；
3. **溯源**：knowledge_source_uri 必须 `knowledge.uri_prefix`（默认 `viking://resources/`）前缀；
4. **分层**：目标汇总层(dws/ads)消费下游汇总层 → 需中间表；
5. **验证证据阈值**：sql_count≥2（可配置）、PK join_match_rate≥0.995 且 null_rate=0、
   维度≥0.95；低于阈值必须有解释（否则 FAIL）；
6. **证据比对**：`verification/evidence.json` 与 sds_design.json 逐条 abs_tol=1e-9，
   `.sql` 文件数 ≥ sql_count；不一致 = **HALTED（人工核查）**；
7. **知识缺口**：非空须 ≥3 条检索轨迹且至少 1 条命中知识库根；
8. **增量沉淀候选**：generate+declared_source / verify+inaccurate → 须 synced|declined。

三门：知识缺口复核门（kb_round≤2，超限 blocked）、增量沉淀门（synced/declined）、
确认门（AskUserQuestion → confirmed=true）。终版 = 贴 工单评论
`【数据概要设计 vN】`（锚头+说明书+json 机读块+确认行）→ verify-latest 回读。

### P2 架构门（validate_phase2_architecture）

检查 `architecture_doc.md` 存在且非空、含难度级别（与 `state.level` 一致）、L2/L3 有
数据链路图（mermaid 或图片）、无占位符；`pipeline.html` L2/L3 必须有。

**交付表清单（PIPE-002）**：文档里必须有一个 ```json 机读块声明本单要交付的全部表：

````markdown
```json
{"tables": [{"name": "dwd_user_act_di", "action": "create"}]}
```
````

只强制 `name`（`action`/`layer` 允许写但不校验）。缺失、非法 JSON、空数组、缺 `name`、
重复表名 → `arch.tables.missing` FAIL。

**P3 出口做等值校验**：`ddl/` 的表名集合必须与这份清单**完全相等**——
`dev.arch.table_missing`（声明了没造）/ `dev.arch.table_extra`（造了没声明）分别报出。
另外 SDS 的 `target_table.name` 必须出现在交付表里（`dev.sds_target_not_built`）。
中途增减表必须回跳 P2 改清单（`advance --allow-back --reason`），不允许在 P3 里改设计。
这样 P2 才真正约束 P3——在此之前 `architecture_doc.md` 除 P2 门自己以外没有任何读取方，
设计写 A/B/C、实际造 X/Y/Z 两道门都会放过。

### P3 SQL 17 项（sql_compliance_check）

零容忍 9 条：

| # | 规则 |
| --- | --- |
| 1 | 文件名与目标表（`-- table:` 或首条 DDL/INSERT 目标）一致 |
| 2 | dp_task 周期任务禁 ALTER TABLE |
| 3 | UPDATE/DELETE 无 WHERE 禁写 |
| 4 | 禁硬编码生产库（prod 标识且未标 -- env: prod） |
| 5 | 语法护栏：括号/引号配对 |
| 6 | 写表前 ddl/ 必须有对应 CREATE TABLE |
| 7 | INSERT…SELECT 禁 SELECT * |
| 8 | dp_task 必须有 `-- schedule:` |
| 9 | 必须有 `-- source:` 血缘注解 |

软提醒 8 条（WARN）：WHERE 过滤、超长行、分号、CTE、聚合、table 注解、分区字段、多语句。

第 8 条（dp_task 的调度注解）按 `config.scheduler.kind` 分目标校验（COMPAT-010）：
`cron`（默认，只要非空 `-- schedule:`，与改动前逐字一致）、`airflow`/`dolphinscheduler`
（追加必填 `-- schedule-id:`，建议 `-- schedule-timezone:`）、`dataworks`（建议 `-- schedule-depends:`）、
`none`（无调度器：不要求注解，写了则 WARN 提醒没人消费）。规则表在 `engine/core/schedulers.py`，
未知 kind 直接 FAIL 并列出可用项。

### 引擎维度：注册表 + 按方言的规则（COMPAT-001/002/003）

基线 17 项是**引擎无关的离线纪律**；"这条 SQL 在这个引擎上成不成立"是另一层，落在
`engine/core/engines.py`（注册表）与 `engine/core/dialects.py`（方言规则）：

```
platform.kind ──► engines.resolve(kind) ──► {family, dialect, required, default_port, evidence, formats}
                                   │
                                   ├─ family=dbapi   → SqlPlatformClient（pymysql 直连）
                                   ├─ family=command → CommandPlatformClient（argv 模板执行 SQL 文件）
                                   ├─ family=cli     → LivePlatformClient（JSON stdout 契约）
                                   └─ family=mcp     → 拒绝直调（会话内通道）
                                   │
                                   └─ dialect ───────► dialects.rules_for(dialect) → 额外 Issue（FAIL/WARN）
```

三条硬约定：

1. **未登记的 kind 一律报错并列出可用项**。曾经的实现在 live 模式静默落回 `LivePlatformClient`，
   于是 `kind=clickhouse` 这类写法会拿到一个语义完全不同的客户端，证据级别从服务端 query_id
   悄悄降级成本地回执而不留痕。
2. **`--dialect` 不传就完全不变**：方言规则是"加一档"，不是"改一档"；基线 17 项在任何引擎下都跑。
   缺省方言按 `platform.kind` 推导，推不出来退回 `generic`（只有基线）。
3. **证据级别写进注册表并在回执里盖章**（`server_query_id` / `local_receipt` / `none`）。
   dbapi 与 command 族拿不到服务端 query id，回执里带 `evidence_level=local_receipt`——
   它只证明"这条 SQL 在本机执行过"，不等于平台 query_id，报告里必须照实写。

配置体检入口：`ddp.py platform doctor`（按注册表逐项判 kind / 必填键 / profiles / 明文口令，
只判"配没配对"，不判"连不连得上"）。分族示例见 `engine/templates/platform.example.json`。

### 类型映射与 DDL 渲染（COMPAT-004）

`engine/core/ddl.py` 是类型与建表模板的唯一权威：

```
canonical 类型（string/bigint/int/double/decimal/boolean/date/timestamp）
   └─► TYPE_MAP[dialect][canonical] → 物理类型（Doris DECIMALV3 / Hive DECIMAL /
                                        ClickHouse Decimal / PostgreSQL NUMERIC …）
   └─► render_ddl(spec, dialect)    → 按方言拼 CREATE TABLE（含该方言的注释/键/属性语法）
   └─► validate_ddl_types(sql, d)   → 拦住"照着别的引擎抄类型"（DECIMALV3 抄到 Hive）
```

三条边界，写在代码与文档里：

1. **渲染产物是起始模板，不是可直接上线的建表语句**：表模型、分布键、分桶数、connector 参数
   由人拍板（`spec.properties` 原样透传），渲染结果照样要过 P3 的 `sql check` 与真实执行；
2. **渲染默认不落盘覆盖**：`ddl render --out` 目标已存在时必须显式 `--force`，
   避免模板把人工改过的建表语句冲掉；
3. **方言语法差异显式表达**：PostgreSQL 的注释生成独立 `COMMENT ON` 语句、Doris/StarRocks 的键子句
   写在 `)` 之外、MySQL 不生成 PROPERTIES、Trino 用 `WITH (format=…)` 而不是 Spark 的 `USING`——
   这几条都有回归用例（`tests/test_ddl.py::RenderTemplateTests`）。

### 湖表格式维度（COMPAT-011）

`engine/core/formats.py` 把 Iceberg / Hudi / Paimon / Delta 的差异写成数据，
`platform.format`（缺省 `none`）是它的配置入口；**格式与执行引擎正交**，
"能不能组合"由注册表判定（`engines.validate_format`），未登记的组合一律报错：

```
kind=spark + format=iceberg   ✅ 登记过        kind=hive + format=iceberg   ❌ 报错并列出可用格式
kind=flink + format=paimon    ✅ 登记过        kind=spark + format=snowflake ❌ 未知格式
```

三处消费点：

1. `platform doctor` 校验组合（配错在体检就能看到，不用等建表）；
2. `ddl render` 按格式插建表片段（spark 的 `USING iceberg`、Flink 的 `connector` 注入、
   Trino 的 catalog 说明），并把写入提示与运维清单挂进 warnings（过期快照、compaction、VACUUM…）；
3. `sql check --format` / P3 出口门按格式出 WARN（Hudi 缺 primaryKey、Delta 的 VACUUM、
   Iceberg 的行级 DELETE、Paimon 缺 bucket）。**全部是 WARN**——格式差异表达"有条件成立"，
   不是语法错，不该阻断流水线。

### P4 机器证据门（最锋利的一刀）

`test_report.md` 标 ✅ 的每个 `TC-xxxx`，必须在 `test_runs.json` 找到
**同 test_id + status=SUCCESS + 非空 query_id**；任一 FAILED 记录直接 FAIL。
用例数下限按难度只是 WARN。workflow `phase4-test.js` 用 schema 强制每个
`data-tester` 返回 query_id——无 query_id 的结果会被降级成 FAILED 记录，堵“只写报告标✅”。

### P5 部署门

`publish_gate` 按环境分级：`env=prod` 需要 `[已确认发布]` token；改动存量任务必须
有 `code_diff.html`；autopilot 生产发布必须有 `review_report.html`。非生产
（dev/test/qa/staging）直接执行并留证据，不需要发布 token。`confirmation_gate` 在
AskUserQuestion 期间锁死交付物写入与生产 CLI。`_state.json` / `_context.md` /
`autopilot_decisions.md` 由 `state_write_guard` 常驻拦截（工具 Write 一律拒绝）。
`.data-dev/engine/**` 由 `engine_readonly` 拦截；`.data-dev/runtime/**` 由
`runtime_guard` 拦截（只允许引擎写入）。`/setup` 记录 `.engine-manifest.json`
哈希基线，`ddp.py` 启动自检引擎副本完整性（engine_integrity），未初始化工单目录
写入被 `ticket_not_initialized` 拦截。
`deploy_plan.json` 写时过 9 条规则（ticket_id / env ∈
dev|test|qa|staging|prod / items 非空 / action 合法 / alter 带 ddl_diff / backfill
带 range / 生产 publish_confirmed=false 仅 WARN…）。
生产 create/alter 可声明 `rollback_sql_file`（`ddp.py rollback` 执行）；
`deploy.require_rollback=true` 时缺失即 FAIL。

后半程两道门（PIPE-004）：

- **回刷**：`validate_phase5_deploy` 逐项比对 `deploy_plan.json` 的 backfill 项与
  `.data-dev/runtime/backfill_runs.json`（key = `表名|start|end`），缺成功记录即 FAIL；
  `mode=live` 且适配器不写账本（kind=cli 外部 CLI）时降级为 WARN。
- **DQC 布防**：`deploy_plan.json` 里每个 `dqc_publish` 项必须在
  `<req_dir>/dqc_results.json` 有 `deployed=true` 的对应记录，缺即 FAIL。
  这一层是"结构校验 + 留痕"，DQC 目前没有引擎侧账本可交叉核验（已知弱点）。

流程开关：`config.modeling.layer_policy=disabled` 时跳过 P1 分层校验与 L3 文件名
前缀要求（适合 MySQL/Doris/Paimon 无分层场景）；`config.semantic.enabled=false`
时 P6 出口只做交付登记，不要求语义层摘要。

**hook 启动器与"装好了但没生效"**：`hooks/*.json` 的命令默认 `py -3`（Windows）。
macOS/Linux 上没有 `py`，hook 命令失败而宿主按 fail-open 放行，结果是插件装好了但
十几道硬门一道都不生效。所以：(1) 非 Windows 装完必须跑 `tools/adapt_hooks.py`；
(2) `tools/adapt_hooks.py --check` 是体检入口，启动器不在 PATH 上就返回非零；
(3) CI 用 ubuntu + windows 双平台矩阵，把 `hooks.json` 里的命令在真 shell 里跑一遍
（`tests/test_hook_wiring.py`），断言"裸调平台 CLI 被拒、只读检索提到这些词不被误伤"。
这三条合起来才覆盖"hook 层到底有没有在工作"。

**setup_guard 只认段首命令词**：未 `/setup` 时裸调
`tracker/platform/quality/openviking` 会被拦，但判定只发生在**段首命令词**上
（支持 `./platform`、`& "C:\\x\\quality.exe"`、`FOO=1 sudo tracker` 等写法）。
命令里只是提到这些词——例如 `rg -n "platform" engine/core/sql_platforms.py` 或
`rg quality`——不再被拦。改之前是整条命令做子串匹配，只读分析会被误伤。

`mode=live` 时 P3/P4 强制要求 `.data-dev/runtime/executions.json` 执行账本存在
（测试/ETL 必须经 `engine platform run-sql`），否则出口 FAIL；`status --gate`
可直接对当前阶段跑 stage_gate 诊断缺项。

**证据的两条约束：绑定产物版本 + 保鲜期（PIPE-001 / PIPE-003）**

执行账本每条记录都带 `sql_hash`（该次真跑的 SQL 内容指纹）。出口门做的判定按阶段分开：

| 阶段 | 查 query_id 在账本里 | 查时效（`evidence_max_age_hours`，默认 24h） | 查绑定（指纹 == 当前 SQL） |
| --- | --- | --- | --- |
| P3 | 是 | 是 | 是 |
| P4 | 是 | 是 | 是 |
| P5 | 否 | **否**（见下） | 是 |

- **绑定**：把账本里该 `query_id` 的 `sql_hash` 与当前 `etl/` `dp_task/` 下 SQL 的内容
  比对。不一致说明"过门之后改过代码"，判 `evidence.binding.stale` FAIL，必须重新真跑。
  旧账本缺 `sql_hash` 时降级 WARN。
- **时效只在 P3/P4 判，P5 不重复判**。理由：时效回答的是"dev 是什么时候跑的"，
  P4 已经判过；而 P5 要回答的是"要部署的是不是被测过的那一份"，那是绑定问题。
  如果 P5 也判时效，一个跨天推进的正常工单会因为"跑得早"被拦，而它要部署的代码
  其实已经被 P4 验证过。**代价**：P4 到 P5 之间不再受保鲜期约束——但复制或改动
  `etl_dev_exec.json` 在 P5 阶段是被 `gates.PHASE_FILE_MAP`（只允许 3/4 阶段写）
  和 `runtime_guard` 挡住的，所以这里读到的仍是 P4 判过的那份证据。

### P6

AI 只能出建议书；进入语义层必须用户拍板（AskUserQuestion，autopilot 不旁路）。
`semantic_update_summary.md` 缺/少于 10 行/无模型信息 → FAIL；kb-publish 留痕、dpsl
校验、OV URI、用户确认记录是 WARN（真实门在 knowledge-base 插件侧，见
REPRODUCTION-NOTES 的边界说明）。

## 4. Hook 消费的 context 键

| context 键 | 谁写 | 谁读 |
| --- | --- | --- |
| `verify_result` | `evidence verify`（etl_dev_exec.json SUCCESS+query_id + 执行账本交叉核对） | submit_guard、P3 stage_gate |
| `self_check_result` | `evidence selfcheck`（diff 展示核对） | submit_guard、P3 stage_gate |
| `user_confirmed` | `evidence confirm`（_user_confirmation.md 含 [已确认]） | submit_guard |
| `code_diff.required` / `code_diff_done` | 引擎 diff 命令 | publish_gate、P3 stage_gate |
| `sql_compliance_result` | PostToolUse 验证器 | 软反馈 |
| `test_evidence_ok` | PostToolUse 验证器 | 软反馈 |
| `deploy_plan_valid` / `deploy_executed` | PostToolUse 验证器 | 软反馈 |
| `tracker_transitioned` | deploy 命令 | P5 stage_gate（WARN） |
| `semantic_*` | semantic 命令 | 汇报 |

闭环：PostToolUse 不能阻断 → 验证器把结果写成 context 键 → PreToolUse 硬门消费这些键 →
“软提示产出状态，状态被硬门消费”。

## 5. 外部系统适配层（engine/core/clients.py）

| 系统 | mock | live |
| --- | --- | --- |
| 工单/评论/流转 | `.data-dev/mock/tracker/workitems.json` | `tracker show / comment / set-status…`（JSON stdout） |
| platform 运行/任务/发布/回刷 | `.data-dev/mock/platform/*.json`（确定性 query_id/run_id） | `kind=cli`：JSON stdout；MySQL 协议族/命令族按 `platform.base_env + profiles.<env>` 路由连接，未配置目标环境即拒绝 |
| MCP（预留） | — | `kind=mcp`：会话内模型调 `mcp__<server>__<tool>`；契约见 REPRODUCTION-NOTES.md 1.2.2，引擎不直调 |
| 知识库 knowledge | `.data-dev/mock/kb/index.json`（只允许 `knowledge.uri_prefix`） | `knowledge.http`：`/api/v1/search/find` + content/read；`knowledge.ov-cli`：`ov find/read -o json`；`custom-cli` 兼容旧包装 |

live 模式大输出落盘 `.data-dev/.cli-output/`。所有 core 代码不依赖传输方式。

## 6. 确定性 Workflow

- `phase4-test.js`：无 cases 拒跑 → 每 case 一个 data-tester agent（schema 强制
  query_id）→ 汇总 runs → 返回 report_rows，主 agent 写盘后 advance；
- `phase5-deploy.js`：无 plan/缺确认 token 拒跑 → 并行 dqc-deployer（dqc_publish 项）
  与 backfiller（backfill 项）→ 返回证据与下一步（写 delivery_summary → advance P6）。

## 7. 测试

```bash
python -m unittest discover -s tests -v
```

覆盖：状态机前向/回跳/完成锁定、SDS 八规则（URI 铁律、evidence HALTED）、SQL 零容忍、
submit 质量门、publish_gate、kb_scope_guard、difficulty/dev_order/confirmation 写门、
deploy_plan 校验、hook 冒烟（PreToolUse 阻断/放行、PostToolUse 同步）、
interactive & autopilot 端到端 demo。
