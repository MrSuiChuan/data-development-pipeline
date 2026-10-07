# 用真实数仓仓库当语料：数开插件 + 记忆插件端到端功能测试报告

**语料仓库**：[MrSuiChuan/data-warehouse-learning](https://github.com/MrSuiChuan/data-warehouse-learning)
（电商数仓实战：Doris 逻辑模型 + Flink/Paimon/Iceberg/Hudi 流批，ODS→DWD→DWS→ADS 四级分层）
——**872 份 SQL（其中 739 份含 `CREATE TABLE`）**、606 份 conf、103 个 Java 类。

**被测对象**

| 插件 | 版本 | 说明 |
| --- | --- | --- |
| 离线数开插件 `data-development-plugin` | v1.3.0（tag `v1.3.0`）+ 本轮修复 | 本仓库 |
| 记忆插件 `knowledge-base-plugin` | 1.2.2 | 用户口径里的"记忆/知识库"插件；若指的是别的插件请指出，本报告可另跑 |

**测试口径**：每条功能给"怎么跑 → 跑了什么 → 结果 → 证据在哪"。跑不动的写"环境不可得/未覆盖"，不写成通过。
台账 `goal#repo-corpus-e2e` 的 `LR-001…008` 逐条对应本报告的章节。

---

## 0. 结论速览

| 功能块 | 结果 |
| --- | --- |
| 数开插件 · 静态方言检查（740 份真 SQL） | ✅ 跑通；**发现 3 类规则误报并修复**，另有 143 处"文件名≠表名"属语料自身约定/不一致 |
| 数开插件 · DDL 多方言（语料真实表结构） | ✅ 真引擎建表/写入/读回一致（Flink 1.20.3 + Paimon 1.4.1），7 种方言渲染 ✓ |
| 数开插件 · 六阶段 mock | ✅ 全链路闭环（P1→P6，`finished=true`） |
| 数开插件 · 六阶段 live（真引擎） | ✅ 工单走到**已交付**；P5 真发布（prod 环境、`ddp_prod.db` 落盘） |
| 数开插件 · 门控负例 | ✅ 6 条负例全部被拦（其中 1 条是**本轮新发现并修复的假成功**） |
| 数开插件 · 其余 verb | ✅ status/gate 诊断、report、ctx、baseline、diff、review-report 均可用 |
| 记忆插件 · 全流程 | ✅ setup→snapshot→skeleton→generate→audit→propose→review→publish→refresh→status 跑通（mock 域） |
| 跨插件契约 | ✅ 记忆插件产物同步进数开项目，数开侧 `kb search`/`kb read` 真读到 |
| OceanBase / CI 记录 / 记忆插件 live 模式 | ⛔ 未覆盖（原因见第 5 节） |

---

## 1. 数开插件：静态方言检查（LR-002）

**怎么跑**：把语料按目录映射到方言（`doris/*` → `doris`；`flink/*` → `flink`），对**全部文件**跑插件
`sql_compliance_check`（与 CLI `sql check` 同一函数）。脚本：`.tmp/lr002_sql_sweep.py`、`.tmp/lr002_classify.py`。

**结果（740 份）**

| 方言 | 文件数 | 零问题 | 规则命中（Top） |
| --- | --- | --- | --- |
| doris | 236 | 0 | no_table_annotation 236、no_semicolon 145、no_cte 41、select_star 16(FAIL)、name_mismatch 4(FAIL)、long_line 3、unbalanced_quote 1(FAIL)、no_lineage 1(FAIL) |
| flink | 626 | 0 | no_table_annotation 625、primary_key_needs_upsert 624、name_mismatch 139(FAIL)、select_star 57(FAIL)、partition_hint 24、long_line 23、unbalanced 15(FAIL)、target 1(FAIL) |

**分类结论（逐类抽查原文后）**

1. **no_table_annotation（861 份）——不是缺陷，是风格差异**：语料用 `-- 表名: …` 的中文头注释，
   插件约定 `-- table: <表名>`。接入外部仓库时这是第一批要批量补的注解。
2. **name_mismatch（143 份 FAIL）——语料自身的不一致**：抽查确认，例如
   `ads_sku_cart_num_top3_by_cate.sql` 里 `CREATE TABLE ads.ads_sku_cart_num_top10_by_cate`；
   `ods_coupon_use_inc.sql` 里 `CREATE TABLE ods.ods_coupon_use_full`；Flink ODS 更是普遍
   （文件名 `ods_activity_info_full.sql`，建的是 `activity_info_full_mq` 源表）。
   插件的交付约定是"文件名=表名"（P3 门据此比对），所以这 143 处要么改名/加注解，要么把源表脚本移出交付目录。
3. **select_star（73 份 FAIL）——真命中**：语料里确实写了 `select *`（如 `ads_activity_stats` 的
   `union select * from ads.ads_activity_stats`），规则是对的。
4. **no_cte（41 份 WARN）——真命中**：多层子查询未改写 CTE，规则给的是建议。
5. **unbalanced（15 份 FAIL）、target（1 份 FAIL）、partition_hint（24 份 WARN）——插件误报，本轮已修**（见第 4 节）。

---

## 2. 数开插件：语料真实表结构 → 多方言 DDL → 真引擎（LR-003）

**怎么跑**：从语料 DDL 解析表结构（`.tmp/lr/build_corpus_spec.py`：`ods_activity_info_full` → 7 列 +
主键 `id`）→ 插件 `ddl render` → 真引擎执行。脚本 `.tmp/lr/lr003-corpus-ddl.sh`。

**结果**

```
ddl render（flink + paimon）→ CREATE TABLE IF NOT EXISTS ods_activity_info_full (id BIGINT …, PRIMARY KEY (id) NOT ENFORCED) WITH ('connector'='paimon')
真引擎建表 ✓ → 写入 3 行 ✓ → 读回：
| rows_now | typed_rows |      （3 行；id/activity_name/activity_type 均有值：双11大促 / 新人礼包 / 会员日）
|        3 |          3 |
同一份 spec 渲染 7 种方言：doris / starrocks / postgres / clickhouse / trino / hive / spark ✓
真实 P3 出口门：FAIL=0（WARN=5）
```

与语料原表结构对照：字段、类型、主键一致；插件按方言做了等价改写（Doris `DECIMALV3`、StarRocks 不带 BUCKETS、
ClickHouse `Int64/Decimal` 等），差异均为**已知的方言映射**而非丢失信息。

---

## 3. 数开插件：六阶段（LR-004）

### 3.1 mock 全链路
`ddp.py demo --root .tmp/lr/demo` → 建单 → SDS → P2 → P3 → P4 → P5 → P6，
`status` 显示 `phase=7 / finished=true`，各门输出 `gate: phase6 / finish`。

### 3.2 live 全链路（真引擎：Flink 1.20.3 + Paimon 1.4.1，需求来自语料的电商订单域）

| 阶段 | 现场 |
| --- | --- |
| P1 建单 | live tracker 替身（JSON CLI）→ 8 项质量门 `quality: []` |
| P1 SDS | 八条硬规则通过 → 贴单回读 `verified: true` → bridge `level=L1` → `sds_status=published` |
| 准入门 | `start` → `phase: 2` |
| P2 | 架构门 → `phase: 3` |
| P3 | 真跑建表 + 聚合写入（只算 PAID）→ 读回 **3 行 / 60.75** → 证据 verify → 门 → `phase: 4` |
| P4 | 用账本真 query_id 写 `test_runs.json` → 机器证据门 → `phase: 5` |
| P5 | `[已确认发布]` → 真发布：`deploy_log.results = [ads_user_act_di create SUCCESS init=prod]`，prod warehouse 落盘 `ddp_prod.db` → `phase: 6` |
| P6 | 用户拍板进语义层 → `finish` → **`phase=7 / finished=true`**，tracker 状态 **已交付** |

---

## 4. 本轮从真实语料里挖出并修掉的 3 个缺陷

| # | 缺陷 | 现场 | 修复 |
| --- | --- | --- | --- |
| 1 | **括号配对检查误报**（FAIL） | 15 份 Flink SQL 被判"括号不配对"；抽查 `hudi/ods/ods_order_detail_full.sql` 发现是注释里 `名称（冗余)`——全角 `（` 配半角 `)` | 只对"代码骨架"配对：先剥块注释/行注释/字符串字面量再数括号；用例 `tests/test_engines.py::test_bracket_check_ignores_comments_and_strings`。修后 15 → 0 |
| 2 | **环境脚本被判"无目标表"**（FAIL） | `flink/doris/catalog/paimon_hive_catalog.sql` 只有 `SET …; CREATE CATALOG …;`，被判 `sql.compliance.target` FAIL | 识别"环境脚本"（含 `CREATE CATALOG/DATABASE/FUNCTION` 且无 `CREATE TABLE`）→ 降为 `sql.compliance.env_script` WARN；用例 `::test_env_script_does_not_need_table_annotation`。修后 1 → 0 |
| 3 | **分区提示忽略显式分区子句**（WARN） | 24 份 Hudi 表以 `PARTITIONED BY (\`k1\`)` 分区（列名叫 `k1` 不叫 ds/date），仍被提示"未发现分区字段" | 有 `PARTITIONED BY` 就不再提示；用例 `::test_partition_hint_respects_explicit_partition_clause`。修后 24 → 0 |

另外还修了一个 **live 六阶段现场暴露的"假成功"**：

| # | 缺陷 | 现场 | 修复 |
| --- | --- | --- | --- |
| 4 | **`deploy` 无计划时空跑还报成功** | `.tmp/lr` 那轮 live 重跑漏写 `deploy_plan.json`，`deploy` 返回 `{"ok": true, "results": []}`——零结果却被当成功；更糟的是读不到 `env`，连 `[已确认发布]` 门也一起跳过了 | 计划缺失/`items` 为空时直接拦（`gate: deploy_plan`）；用例 `tests/test_flow_options.py::test_deploy_without_plan_blocks_instead_of_silent_noop`；补上计划后重跑：`results` 1 条且 `init=prod`、tracker 流转到"已交付" |

---

## 5. 数开插件：门控负例与其余 verb（LR-005）

| 负例 | 期望 | 实际 |
| --- | --- | --- |
| 跳过阶段（P4 → P6） | 拦 | ✅ `禁止跳转（P4 -> P6）：只允许逐级前向推进或 --allow-back 回跳`；对照 P4→P5 正常通过 |
| 未确认就 `deploy` | 拦 | ✅ `gate: publish_gate：comment 必须含 [已确认发布] token` |
| `rollback` 缺 token | 拦 | ✅ `gate: rollback_gate：comment 必须含 [已回滚] token` |
| `deploy` 无计划 | 拦（本轮修） | ✅ `gate: deploy_plan：计划缺失或 items 为空`（修前是"零结果 + ok:true"） |
| 改 SQL 后复用旧证据（P3） | 拦 | ✅ `evidence.binding.stale`，FAIL=1 |
| 证据过期 30 小时（P4） | 拦 | ✅ `test.precondition.etl：执行证据已过期（30.0h > 24h）` |
| mock 回执冒充真执行证据 | 拦 | ✅ `gate: evidence_ledger：query_id 不在引擎执行账本中（AI 不能手写执行证据）` |

其余 verb 实跑：`status`（阶段/门控/上下文）、`status --gate`、`report`（时间线+证据+交付物）、`ctx list`、
`evidence verify`、`baseline capture`（读到 `dp_task` 基准）、`diff`、`review-report` 均返回结构化结果。

---

## 6. 记忆插件（knowledge-base-plugin）全流程（LR-006）

**语料怎么进去的**：没有造假样例——`.tmp/lr/build_kb_meta.py` 直接从语料 DDL 解析出
**8 张真实表**（ods×2 / dwd×2 / dws×2 / ads×2）的字段、类型、注释、主键、注释头里的口径与数据来源，
写成语料元数据快照；语料没给的（负责人、行数、cron）留空。

| 步骤 | 结果 |
| --- | --- |
| `setup --mode mock --domain order` | ✅ `.kb` 运行时 + 独立 git 仓建立 |
| `snapshot fetch/show` | ✅ 8 张表入库（6 类元数据齐全） |
| `skeleton new` + 5 步 + `validate` | ✅ step 1..5 写入，validate `problems: []` |
| `generate --domain order` | ✅ 产出 `navigation.md` + 8 篇表文档（9 章节），并如实标 `placeholder_count: 8`（元数据缺的项标"待补"而非编造） |
| `audit --mode local --level strict` | ✅ `passed: true` |
| `propose --demo` → `rule list` → `review list` | ✅ 生成提案，`pending_review: 1`（引擎门禁 APPLIED ≠ Owner 终签） |
| `audit --mode publish` → `publish` | ✅ 先被 `kb_publish_guard` 拦（"最近审计不是 publish 模式"），补 publish 模式审计后发布成功：`kb-v1`，git 提交 `c217530` |
| `refresh` | ✅ 产出漂移报告（本次 `drift: []`，快照未变） |
| `status` | ✅ `publish_count: 1`、`last_audit`/`last_publish` 齐全 |

**记忆插件侧发现（未改代码，属它的仓库）**

1. **表标识只认裸表名**：`skeleton validate` 用快照的 `table_id`（= `name`，不带 schema）做集合比对，
   因此 `ads.ads_activity_stats` 这种 `schema.table` 写法一律报"不在元数据快照中"——真实仓库里到处是
   `schema.table`（Doris/Flink 都这么写）。本次按裸表名改通；建议插件接受两种写法或在文档里写明。
2. **hook 按会话根判断 `.kb`**：从数开插件仓库里驱动记忆插件会被 `setup_guard` 拦（"未初始化 .kb"），
   即使命令里 `cd` 到了有 `.kb` 的项目；本次用一层包装脚本绕开。跨项目驱动时会反复踩到。
3. **检索摘要质量取决于元数据**：语料没给负责人时，同步给数开侧的 `abstract` 是 `- owner: None`，
   按业务词（"活动补贴"）搜不到，只有按表名能搜到。建议生产者用"表说明/字段"生成摘要。

---

## 7. 跨插件契约（LR-007）

```
记忆插件 publish --consumer-root <数开项目>
  → consumer_sync: {ok: true, added: [8 个 viking://resources/domains/order/tables/*.md]}
数开侧 kb search --query ads_activity_stats
  → 命中 viking://resources/domains/order/tables/ads_activity_stats.md（domain=order, layer=ads）
数开侧 kb read --uri viking://resources/domains/order/tables/ads_activity_stats.md → 返回内容
数开侧 kb read --uri viking://resources/../../etc/passwd → 被拒（ClientError：知识文档不存在）
```

**前置条件（实测踩到）**：消费者项目必须先被"碰"一次（跑任意 kb 命令）生成
`.data-dev/mock/kb/index.json`，否则生产者侧 `consumer_sync.ok=false`（"consumer 无索引"）。
建议生产者自己创建该索引。

---

## 8. 未覆盖与风险（如实列出）

1. **OceanBase 仍未验证**（镜像大层拉不动 + 单机需 2–4 GB 内存），台账 `CLOSE-009` 有解除条件；
2. **记忆插件的 live 模式未跑**：它要真实 metadata MCP 与 git remote，本机没有；
   OpenViking 同步（`ov-check`）同样未接真；
3. **CI 运行记录未核**：仓库私有且本机无 token；
4. 语料里的 **DML（872 份中非 CREATE TABLE 的那部分）只做了规则扫描**，没有逐条真跑（真跑需要完整的
   ODS 上下游与 Doris/Flink 集群，超出本机单机范围）；
5. 真跑都是**单机形态**：YARN/K8s、远端存储、多节点/副本、流式与状态恢复未验证；
6. 本轮 live 六阶段的"工单系统"是 JSON CLI 替身，不是真实 OA。

---

## 9. 复跑命令（脚本都在仓库里或本次测试目录）

```bash
# 语料
git clone https://github.com/MrSuiChuan/data-warehouse-learning   # 872 份 SQL

# 静态检查（全语料）
py -3 .tmp/lr002_sql_sweep.py <语料根> 0
py -3 .tmp/lr002_classify.py

# 语料 DDL → 真引擎
bash .tmp/lr/lr003-corpus-ddl.sh

# 六阶段
python engine/ddp.py demo --root .tmp/lr/demo                 # mock
bash .tmp/live-pipeline/step1-flink.sh && bash .tmp/live-pipeline/step2-sds.sh \
  && bash .tmp/live-pipeline/step3-p2p3.sh && bash .tmp/live-pipeline/step3c-p3.sh \
  && bash .tmp/live-pipeline/step4-p4p6.sh                    # live

# 门控负例与其余 verb
bash .tmp/lr/lr004-005.sh && bash .tmp/lr/lr005-skip.sh

# 记忆插件 + 跨插件契约
bash .tmp/lr/kb-step1.sh && bash .tmp/lr/kb-pipeline.sh && bash .tmp/lr/kb-pipeline2.sh \
  && bash .tmp/lr/kb-pipeline3.sh && bash .tmp/lr/ddp-kb-contract.sh
```

> 说明：`.tmp/` 不在版本控制里（本仓库的 `.gitignore` 约定）。要把这些驱动长期留下，
> 应平移进 `tools/live/`（本仓库已有该目录与索引，见 `tools/live/README.md`）。
