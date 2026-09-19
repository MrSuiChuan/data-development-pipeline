# data-development-plugin 研发编码规范（由 /setup 注入）

本目录受 data-development-plugin 六阶段流水线管理。任何数据研发动作都按下列铁律执行：

1. 顺序铁律：DDL → ETL → dp_task；设计(SDS) → 架构 → 研发 → 测试 → 部署 → 语义层，逐级推进。
2. 证据铁律：说“测过了”要有 `test_runs.json` 的 `SUCCESS + query_id`；说“跑过了”要有 `etl_dev_exec.json`；说“发布”要有 `[已确认发布]` token。报告 ✅ 不等于真跑。
3. 状态铁律：`_state.json` / `_context.md` / `autopilot_decisions.md` 是机器状态，
   只由引擎写入（工具 Write 会被 state_write_guard 拦截）；证据键（verify_result /
   self_check_result / user_confirmed 等）禁止 `ctx set`，只能用 `evidence
   verify|selfcheck|confirm` 由真实产物驱动写入。
4. 不可逆门铁律：生产发布 / ALTER / 回刷 / 进入语义层，必须 AskUserQuestion 让用户拍板；
   autopilot 也不旁路；非生产（dev/test/qa/staging）部署不需要发布确认 token。
5. 环境铁律：SQL/命令平台的执行连接由 `platform.base_env` + `platform.profiles.<env>`
   决定；未配置目标环境禁止执行，防止 DDL/回刷打到错误数据库。
6. 可回滚：生产 create/alter 尽量声明 `rollback_sql_file`；回滚必须 `[已回滚]` token。
7. SQL 零容忍：禁无 WHERE 的 UPDATE/DELETE；周期任务禁 ALTER；禁 SELECT *；写操作前必须只读核对（2h TTL）。
8. 知识源铁律：SDS 的 `knowledge_source_uri` 必须以 `knowledge.uri_prefix`（默认 `viking://resources/`）开头。
9. 文件纪律：交付物只在 `.data-dev/tickets/<工单>/` 内；`ddl/`、`etl/`、`dp_task/` 三目录同名同序。

进度入口：`py -3 .data-dev/engine/ddp.py status --ticket <id>`（Windows）；`python3` 同理。
