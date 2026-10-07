# 真实测试报告：live 模式六阶段端到端（Flink 1.20.3 + Paimon 1.4.1）

**目的**：此前的真跑都是"引擎链路"（doctor → ddl render → 建表 → 写入 → 读回 → 证据 → P3 门）。
本轮把**插件自己的六阶段流水线**放进 live 模式、跑在一个真实引擎上，看门控在真流程里是不是真的拦得住、
连接器是不是真的去调外部系统。台账 `CLOSE-005`。

| 项 | 值 |
| --- | --- |
| 模式 | `mode=live`（不是 `demo` 的 mock） |
| 工单系统 | 自建 JSON CLI（`cli.tracker`，实现 `create/show/list/comment/comments/set-status`）——**测试替身**，但走的是 live 分支 |
| 平台 | Flink 1.20.3 + Paimon 1.4.1，`execution.target=local`，批模式（见 [flink.md](engines/flink.md)） |
| 环境隔离 | dev / prod 两个 warehouse（`platform.profiles.prod.command.run` 换 init 文件） |
| 日期 | 2026-10-07 |
| 结果 | **P1 → P6 全通，工单状态走到「已交付」**；过程中揪出 **3 个真缺陷**，全部修复 |

## 0. 一句话结论

live 六阶段真的跑通了：建单（8 项质量门）→ SDS（八条硬规则 + 贴单回读 + 桥接 + publish）→ 准入门 →
P2 架构门 → **P3 真跑**（Flink/Paimon 建表 + 聚合写入 2 行 / 60.75 + 证据绑定）→ P4 机器证据 →
**P5 真发布**（prod 环境执行）→ P6 语义层（用户拍板）→ finish（工单「已交付」）。
真流程同时暴露三个**只有真跑才会现形**的缺陷，见第 2 节。

## 1. 现场（关键输出）

```
# P1 建单（8 项质量门：章节/要素/负责人/计划日期/长度）
{"ok": true, "workitem": {"id": "T-0001", ...}, "quality": []}

# SDS：八条硬规则 + 证据比对
{"ok": true, "issues": []}                       # sds validate --confirm
{"verified": true}                                # ticket verify-latest（贴单回读）
{"ok": true, "level": "L1", ...}                  # sds bridge
{"ok": true, "sds_status": "published"}           # sds publish --version 1

# 准入门 → P2
{"ticket": "T-0001", "phase": 2, "sds_status": "published", "sds_confirmed": true}

# P3 真跑（Flink + Paimon）
CREATE TABLE ... PRIMARY KEY (user_id, ds) NOT ENFORCED ) WITH ('connector'='paimon')   # 真引擎接受
INSERT INTO ads_user_act_di SELECT user_id, count(*), sum(pay_amt), ds FROM ods_order_detail_di
  WHERE pay_status = 'PAID' GROUP BY user_id, ds;                                        # Job 提交成功
读回：| rows_inserted=2 | amt_sum=60.75 |   明细：u1=2 笔/30.75，u2=1 笔/30.00
evidence selfcheck/confirm/verify → ok
advance --phase 4 → {"ok": true, "phase": 4}

# P4（真 query_id）→ P5 真发布（prod warehouse）
deploy_log.json: {"item": "ads_user_act_di", "action": "create", "status": "SUCCESS", "query_id": "run_6617bbf66b90a0"}

# P6 语义层 + finish
status: {"phase": 7, "finished": true}
tracker: 已交付
```

## 2. 真流程揪出的三个缺陷（都已修复 + 用例）

| # | 现象（现场） | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | SDS 贴单后 `ticket verify-latest` 永远 `verified: false` | 真 tracker 的评论对象字段是 `content_md`（与 mock 同形），契约文档只写了"输出 JSON"没说字段名；另外同一个版本号贴两次时 `max()` 取的是**第一条**（旧的）而不是最新一条 | ① 文档补字段契约；② `verify_latest_comment` 改为"同版本取最后一条"；用例 `tests/test_spec_latest_comment.py` |
| 2 | **P5 声称发布到 prod，实际跑的是 dev**：`deploy_log` 里 stdout 是 `ddp-dev-init.sql`，数据落进 dev warehouse，prod warehouse 空 | `CommandPlatformClient` 在 `__init__` 里只读一次 `platform.command.run`，`profiles.<env>.command.run` 的覆盖被静默忽略（`_effective_config` 也只合并连接类字段） | 每次执行按 env 重新解析 argv，并让 profile 继承/覆盖 `command`；用例 `tests/test_sql_platform.py::test_profile_command_override_is_used_per_env`；真机复验：`--env prod` 走 prod init、`ddp_prod.db` 出现在 prod warehouse |
| 3 | P3 真跑第一次失败：`evidence.binding.stale`（证据与当前 etl/dp_task 不一致） | 不是引擎的错：驱动脚本先跑 DDL 抢了 `etl_dev_exec.json` 的指纹，且 ETL 文件当时还没落到 `etl/`。真流程纪律（**先落盘交付物 → 再真跑 → 再贴证据**）被违反时，门确实会拦 | 修正驱动顺序（三层文件先落盘、ETL 真跑带 `--save-evidence`、读回走"不进账本"的通道）；**门的行为是对的**，无需改代码 |

另外两条被门正确拦下的现场，也值得记一笔（说明门在工作，而不是我把门绕过去了）：

- 上游 fixture 表 `ods_order_detail_di` 用插件通道写入时被 `sql.compliance.ddl_missing` 拦下
  （"写入 X 但 ddl/ 下无对应 CREATE TABLE"）——**上游表不归本工单交付**，改用直接 sql-client 建 fixture；
- 新表场景 `diff` 明确返回"无线上基准（新表可跳过）"，没有伪造 code_diff。

## 3. 这一项**没有**证明什么

- 这里的外部工单系统是**测试替身**（JSON CLI），不是真实 OA/需求系统——证明的是 live 通道会被真的调用、
  契约字段能被消费，不是"接了某家真实工单系统"；
- 知识库连接器只声明未使用（SDS 的 verification evidence 由交付物提供），没有连真实 KB；
- 质量（DQC）连接器未涉及：本次 deploy_plan 里没有 `dqc_publish` 项；
- 引擎是单机 Flink + 本地 Paimon catalog，不涉及集群/YARN/K8s 与远端存储；
- 一切证据级别仍是 `local_receipt`（command 族拿不到平台 query_id）。
