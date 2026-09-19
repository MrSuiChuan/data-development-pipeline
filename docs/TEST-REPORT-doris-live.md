# data-development-plugin × Apache Doris 端到端测试报告

| 项目 | 内容 |
| --- | --- |
| 测试日期 | 2026-09-13 |
| 被测对象 | `data-development-plugin`（通用离线数据研发流水线插件，engine 版本见 CHANGELOG 1.2.1） |
| 测试模式 | `mode=live`（非 mock，真实 SQL 执行） |
| 数仓环境 | WSL2 Debian 上的 Apache Doris 4.0.8（FE + BE 单机） |
| 执行入口 | `engine/ddp.py`（插件公开 CLI 契约，逐条 verb 调用） |
| 结论 | **六阶段流水线 31 步全部通过（0 失败），并在真实 Doris 上产生真实副作用（ALTER / INSERT / 发布 / 回刷 / 工单流转）** |

## 0. 一句话结论

把 Doris 当成"真实数据平台"，把本地 JSON-CLI 当成"真实工单系统"，插件在 **live 模式**下从建单一路走到语义层闭环：31 个步骤全部通过，P1–P6 出口门全部由机器证据放行；同时对抗式测试暴露了 5 个缺陷（其中 2 个是阻断级），已全部修复并回归验证。

## 1. 测试目标

1. 验证 **live 模式**下六阶段状态机与硬门控能否在真实数仓上端到端跑通（mock 之外的真实路径）。
2. 验证 **机器证据闭环**：执行账本 `query_id` ↔ `test_runs.json` ↔ 阶段出口门是否真正咬合。
3. 验证**官方工具链**（doris-mcp-server / doris-cli / doris-skills）可用性与权限边界。
4. 对抗式验证：不预设"应该能跑"，凡是被门拦住的，先判定是"证据不足"还是"产品缺陷"。

## 2. 测试环境

```text
## WSL
Linux 6.6.87.2-microsoft-standard-WSL2 x86_64
wsl_ip=<本机 Doris 地址>
java=openjdk version "17.0.2" 2022-01-18
max_map_count=2000000
mem=7.7Gi

## Doris
              Host: <本机 Doris 地址>
             Alive: true
           Version: doris-4.0.8-rc02-bc8ea1bac6d
                   Host: <本机 Doris 地址>
                  Alive: true
                Version: doris-4.0.8-rc02-bc8ea1bac6d

## 关键配置（fe/be）
30:JAVA_OPTS_FOR_JDK_17="-Dfile.encoding=UTF-8 -Djavax.security.auth.useSubjectCredsOnly=f
33:JAVA_HOME=~/doris
52:priority_networks = <LAN-IP>/20
97:mem_limit = 2g
57:priority_networks = <LAN-IP>/20

## 端口
0.0.0.0:9060
0.0.0.0:9050
0.0.0.0:8040
*:9030
*:8030
```

补充说明：

- WSL2 的 loopback 与 Windows 主机隔离，Windows 侧访问 Doris 必须用 WSL 的 eth0 地址（`<本机 Doris 地址>:9030`），`localhost` 转发在这台机器上不通。
- `priority_networks` 必须设置成 eth0 网段（`<LAN-IP>/20`），否则 BE 会被判 Dead。
- FE 堆从默认 `-Xmx8192m` 下调到 `2048m`、BE `mem_limit=2g`：这台 WSL 总共 7.7Gi，且同时跑着 OpenSearch / ActiveMQ / Redis / Docker。
- `vm.max_map_count=2000000` 需要 root 设置，已固化进 `/etc/wsl.conf` 的 boot command（BE 启动脚本硬校验，低了直接 `exit 1`）。

## 3. 连接器配置（`.data-dev/config.json` 摘要）

```json
{
  "mode": "live",
  "cli": {
    "tracker": ["py", "-3", "<项目根>/tools/ddp_tracker.py"],
    "knowledge": "knowledge"
  },
  "platform": {
    "kind": "doris",
    "base_env": "dev",
    "host": "<本机 Doris 地址>",
    "port": 9030,
    "user": "root",
    "database": "ddp_dev",
    "profiles": {
      "dev":  { "database": "ddp_dev" },
      "prod": { "database": "ddp_prod" }
    }
  },
  "knowledge": { "uri_prefix": "viking://resources/", "kind": "custom-cli" },
  "limits": { "evidence_max_age_hours": 24 }
}
```

设计要点：**dev / prod 用同一台 Doris 的两个库隔离**，这样 P3 的开发执行落在 `ddp_dev`、P5 的发布与回刷落在 `ddp_prod`，环境隔离代码路径（`base_env` + `platform.profiles`）被真实走到，而不是纸面声明。

## 4. 业务场景与前置数据

场景：订单明细 `ods_order_src` → 按省域/日期汇总表 `dwd_order_province_di`，本次为 **L1 改造**（新增"客单价" `avg_amount` 列并改造 ETL）。

| 表 | 库 | 初始状态 |
| --- | --- | --- |
| `ods_order_src` | ddp_dev / ddp_prod | `20260901` 3 行、`20260902` 1 行、`20260903` 3 行 |
| `dwd_order_province_di` | ddp_dev / ddp_prod | 4 列（dt/province/order_cnt/total_amount），历史 `20260901`、`20260902` 分区 |

每次跑测试前，驱动脚本会把两张目标表重置回"未加列"状态，保证可重复。

## 5. 官方工具链验证

### 5.1 doris-mcp-server 1.0.0（官方 MCP Server）

- 安装：`python3 -m venv ~/doris/tools/mcp-venv` + `pip install doris-mcp-server==1.0.0`
- 传输：stdio（Codex 用）与 Streamable HTTP（`/mcp`、`/live`、`/ready`）都支持；本机注册进 Codex 的是 stdio。
- 暴露 **8 个工具域、55 个子工具**（`doris_catalog` / `doris_query` / `doris_cluster` / `doris_pipeline` / `doris_search` / `doris_governance` / `doris_lakehouse` / `doris_semantic`）。
- 实测（工具清单 + 真实查询）：

```text
TOOL_COUNT 8
IS_ERROR False
RESULT {"child_tool":"execute_query","data":{"data":{"columns":[{"name":"province"},{"name":"cnt"},{"name":"amt"}],"row_count":2,"rows":[{"amt":"600.50","cnt":3,"province":"广东"},{"amt":"325.25","cnt":4,"province":"浙江"}],"truncated":false},"metadata":{"execution_time_seconds":0.004878520965576172,"limits":{"max_bytes":1048576,"max_rows":10,"timeout_seconds":30},"operation":"SELECT","protocol":"mysql","result_bytes":103,"source":"doris_query","truncation_reason":null},"status":"success","warnings":[]},"domain":"doris_query","manifest_version":"query.d5533d6958e2","metadata":{"duration_ms":18.659,"request_id":"req_f7945e838cb3e87a","source":"doris","truncated":false},"mode":"result","warnings":[]}
```

握手验证（用 Codex 实际启动路径 `wsl.exe -e ~/doris/tools/start-doris-mcp.sh`）：返回 `protocolVersion 2025-06-18` 与 tools 能力，说明注册可用（Codex 需重启会话加载）。

### 5.2 doris-cli 0.1.2（官方 CLI）

```text
# doriscli auth status --env local
{
  "all_environments": [
    "local"
  ],
  "backends": [
    {
      "alive": true,
      "host": "<本机 Doris 地址>",
      "id": 1789000752410
    }
  ],
  "changed_session_vars": 0,
  "doris_version": "5.7.99",
  "environment": "local",
  "host": "127.0.0.1",
  "http_port": 8030,
  "http_probe": {
    "legacy": {
      "ok": true,
      "status": "ok",
      "url": "http://127.0.0.1:8030/api/health"
    },
    "rest_v2": {
      "ok": true,
      "status": "ok",
      "url": "http://127.0.0.1:8030/rest/v2/manager/query/query_info?is_all_node=true"
    }
  },
  "http_status": "connected",
  "mysql_latency_ms": 1,
  "mysql_port": 9030,
  "mysql_status": "connected",
  "source": "flag",
  "sour

# doriscli sql --env local "SELECT province, COUNT(*) cnt, SUM(amount) amt FROM ddp_dev.ods_order_src GROUP BY province"
{
  "columns": [
    "province",
    "cnt",
    "amt"
  ],
  "exec_time_ms": 25,
  "query_id": "41ac9ac6058b430b-98b2afae4f64aa3f",
  "rows": [
    {
      "amt": 600.5,
      "cnt": 3,
      "province": "广东"
    },
    {
      "amt": 325.25,
      "cnt": 4,
      "province": "浙江"
    }
  ],
  "rows_returned": 2
}
```

注意点：`doriscli sql` 返回的是 **Doris 服务端真实 `query_id`**；而插件通过 pymysql 直连时拿不到服务端 query_id，只能生成执行回执（`run_<hash>`）——这是插件文档里已声明的诚实边界，两者可以互补。

### 5.3 doris-skills（官方 Agent Skills）

5 个 skill 已安装到 Codex skills 目录：`doris-best-practices`、`doris-architecture-advisor`、`doris-debug`（含 10 个子 skill）、`doris-profile-reader`、`doris-repo-review`。

### 5.4 只读账号权限边界（MCP/CLI 都用它，不用 root）

| 操作 | 命令 | 结果 |
| --- | --- | --- |
| 查询 | `SELECT COUNT(*) FROM ddp_dev.ods_order_src` | `7` ✅ |
| 建表 | `CREATE TABLE ddp_dev.denied_probe(id INT)` | `ERROR 1105 (HY000) at line 1: errCode = 2, detailMessage = Access denied; you need (at least one of) the (CREA` |
| 删表 | `DROP TABLE ddp_dev.ods_order_src` | `ERROR 1105 (HY000) at line 1: errCode = 2, detailMessage = Access denied; you need (at least one of) the (DROP` |

## 6. Claude Code 真实宿主测试（补测：命令层 + Hook 层）

前面几章走的是插件的**引擎层**（`engine/ddp.py`，也就是命令与 hook 共同调用的那份实现）。
插件声称的三层防线里，第 1 层（命令编排）与第 3 层（Hook 硬门）只有跑在宿主里才算真的被测过。
本轮补测用本机已安装的 **Claude Code 2.1.231** 加载插件本体：

```bash
claude --plugin-dir <插件根> -p "<prompt>" --permission-mode bypassPermissions
```

### 6.1 命令层：`/data-development-plugin:setup`

宿主的斜杠命令带插件命名空间（`/data-development-plugin:setup`，不是 `/setup`）。执行后工程被真实初始化：

```text
生成：.claude、.data-dev、CLAUDE.md、_patch.py、capture_cc_evidence.py、cc_block_test.log、cc_gate_test.jsonl、cc_gate_test2.jsonl、cc_gate_test3.jsonl、cc_hook_run.log、cc_hook_run3.log、cc_hook_run4.log、cc_hook_run5.log、cc_hook_run6.log、cc_publish_test.log、cc_skills_check.log、cc_submit_stream.jsonl、control_ok.txt、control_out.log、ddp-hook-trace.jsonl、evidence、hook_probe.py、logs、retest2_out.log、retest_out.log、trace6.jsonl、描述-1.md、描述-7.md
.data-dev/ 内含：engine（引擎副本，带去重清单）、runtime（引擎执行账本）、.runtime（hook 账本）、
tickets/、mock/、config.json
```

其中 `.claude/`（权限白名单等注入）与 `CLAUDE.md`（编码规范/文件纪律注入）都是插件的 `/setup` 产物，
证明"命令编排层"在真实宿主里可执行。

### 6.2 命令层：`/data-development-plugin:status`

`/status` 在宿主里读状态机并诊断当前阶段出口门，实测输出（节选）：

```text
dence.missing` — `etl_dev_exec.json` 缺失或非法（缺开发环境真跑证据）
2. `dev.self_check.pending` — `context.self_check_result != passed`（自检未过）
3. `dev.verify.pending` — `context.verify_result != passed`（离线引擎重跑验证未过）

**⚠️ 1 个 WARN（非阻塞）：**
- `dev.code_diff.new_task` — 新表场景无线上基准，跳过 code_diff 强校验

## 下一步

门未通过，Phase 3 数据研发尚未产出任何证据。请执行研发动作：

```
/data-development-plugin:data-develop
```

该命令会引导完成 `ddl/` → `etl/` → `dp_task/` 三目录产出，随后：
- 开发环境真跑 → 生成 `etl_dev_exec.json`（解 `exec_evidence.missing`）
- `self_check` → 写入 `self_check_result`（解 `self_check.pending`）
- 离线引擎重跑 → 写入 `verify_result`（解 `verify.pending`）

需要我直接启动 Phase 3 研发吗？
Ignoring 9 permissions.allow entries from .claude/settings.json: this workspace has not been trusted. Run Claude Code interactively here once and accept the trust dialog, or set projects["C:/Users/<user>/Documents/ChatGPT/ddp-cc-plugin-test"].hasTrustDialogAccepted: true in <本机路径>\.claude.json.
"deepseek-v4-pro" is not a model this version of Claude Code recognizes, so auto-compact will keep this session within 200k tokens (the context window it assumes). If the model accepts more, append [1m] to the model name for 1M, or set CLAUDE_CODE_MAX_CONTEXT_TOKENS to its real window; to make it recognized, map it in the modelOverrides setting or update Claude Code; CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1 restores the previous wait-for-the-API behavior.
```

可以看到它并没有"报平安"，而是直接列出 Phase 3 的 3 个 FAIL blocker（缺 `etl_dev_exec.json`、
`self_check_result != passed`、`verify_result != passed`）与 1 个 WARN，并给出下一步建议——
说明状态机 + 校验器在宿主里是活的。

### 6.3 命令层：`/data-development-plugin:submit`（建单入口）

在宿主里让模型按 `/submit` 的剧本建单，事件流显示它是**照命令脚本逐步执行**的：

```text
TEXT: I'll follow the `/submit` workflow. First, verify setup is complete.
TOOL: Glob {"pattern": ".data-dev/engine/ddp.py"}
TOOL: Glob {"pattern": ".data-dev/**/*.json"}
TEXT: Setup is complete. Let me check the current state and config to understand mode and ticket numbering.
TOOL: Read {"file_path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\current.json"}
TOOL: Read {"file_path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\config.json"}
TOOL: Read {"file_path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\mock\\tracker\\workitems.json"}
TEXT: Setup complete, mock mode, `seq: 6` → next ticket is T-0007. Let me check the engine's ticket-create quality gates to craft a passing description.
TOOL: Grep {"pattern": "质量|quality|gate|要素|负责人|计划日期", "path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\engine", "outpu
TOOL: Grep {"output_mode": "content", "path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\engine\\core\\gates.py", "patte
TOOL: Grep {"-n": true, "output_mode": "content", "path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\engine\\core\\valid
TOOL: Read {"file_path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\engine\\core\\validators.py", "offset": 896, "limit"
TOOL: Grep {"-n": true, "output_mode": "content", "path": "<本机路径>\\d
```

关键动作可核对：先确认 `/setup` 已就绪 → 读 `.data-dev/config.json` 与 mock tracker 得到下一个工单号 →
**去引擎源码里核对 8 项工单质量门**（而不是凭印象）→ 按命令要求把需求写进临时文件 `描述-7.md`。
产物文件 `描述-1.md` / `描述-7.md` 确实落盘。

诚实说明：单次 headless 运行在最后一个 `tool_use` 边界结束（13 轮、约 $0.81），
没有在这一次调用里跑完 `ticket create` 及后续六阶段——宿主全流程需要多次续跑，
本轮把它列为可选后续（见第 11 章）。

### 6.4 Hook 层（确定性对抗探针，9 项）

直接按 Claude Code 的 hook 契约（stdin 收 JSON、stdout 出决策、exit 2 表示硬阻断）给
`hooks/pretooluse.py` 喂事件，并模拟宿主"放行就落盘"的行为：

```text
探针工单 T-0006，current_phase=3, level=L1



[OK ] 越界写 SQL（工作区外）: BLOCK (exit=2, gate=-)

        [data-development-plugin:workspace_guard] 工具调用被硬门拦截。研发 SQL 目录必须在 .data-dev/tickets/<工单> 工作区内。

[OK ] 改写 .data-dev/engine 副本: BLOCK (exit=2, gate=-)

        [data-development-plugin:engine_readonly] 工具调用被硬门拦截。.data-dev/engine 是引擎运行时副本，禁止工具改写（engine_readonly）。

[OK ] 手写 .data-dev/runtime 账本: BLOCK (exit=2, gate=-)

        [data-development-plugin:runtime_guard] 工具调用被硬门拦截。.data-dev/runtime 是机器账本/运行时目录，只允许引擎写入。

[OK ] DDL 未产出先写 dp_task（顺序守卫）: BLOCK (exit=2, gate=-)

        [data-development-plugin:dev_order_guard] 工具调用被硬门拦截。dp_task 先于 ETL：写 dp_task/t.sql 前必须有 etl/t.sql。

[OK ] L1 难度下写 CREATE TABLE（难度守卫）: BLOCK (exit=2, gate=-)

        [data-development-plugin:difficulty_guard] 工具调用被硬门拦截。LL1 禁建表：ddl 目录 CREATE TABLE 被难度门拦截（只允许改存量）。

[OK ] 无确认 token 直接 publish（发布门）: BLOCK (exit=2, gate=-)

        [data-development-plugin:submit_guard] 工具调用被硬门拦截。证据键未齐，禁止提交/发布: verify_result≠passed, self_check_result≠passed, user_confirmed≠True（需先 SQL 验证 + self_c

[OK ] 合规 DDL 写入（L1 用 ALTER）: ALLOW (exit=0, gate=-)

        -> 已落盘 t.sql

[OK ] DDL 就绪后写 ETL: ALLOW (exit=0, gate=-)

        -> 已落盘 t.sql

[OK ] DDL/ETL 就绪后写 dp_task: ALLOW (exit=0, gate=-)

        -> 已落盘 t.sql



结果：9/9 项符合预期
```

结论：**6 个违规动作全部被对应硬门拦住，3 个合规动作按 DDL → ETL → dp_task 顺序正常放行**。
被验证的硬门包括 `workspace_guard`、`engine_readonly`、`runtime_guard`、
`dev_order_guard`（顺序）、`difficulty_guard`（L1 禁止建表）、`submit_guard`（证据未齐禁止提交/发布）。

顺带一个反直觉的发现：第一次探针把"合规 ETL 写入"标记为应放行，结果被 `dev_order_guard` 拦住——
因为 DDL 尚未落盘。**门是对的，是测试预期错了**，这恰好从反面证明了顺序守卫真的在读磁盘状态，
而不是看模型怎么说。

### 6.5 Hook 层（真实会话内拦截）

让宿主的模型真的发出一次违规写入（把周期任务文件写进工单目录、但没有对应的 ETL）：

```text
TOOL_USE(Write): {"file_path": "<本机路径>\\ddp-cc-plugin-test\\.data-dev\\tickets\\T-0003\\dp_task\\order_daily2.sql", "content": "INSERT INTO t SELECT 1;"}
TOOL_RESULT: [data-development-plugin:dev_order_guard] 工具调用被硬门拦截。dp_task 先于 ETL：写 dp_task/order_daily2.sql 前必须有 etl/order_daily2.sql。 | is_error=True
```

要点：模型发出的 `Write` 被 `dev_order_guard` 判 `deny`，`is_error=true`，**文件确实没有落盘**
（`Test-Path` 为 False）。这是"机器不可绕"的现场证据：不是模型自觉，而是 hook 在工具调用上拦下来的。

### 6.6 本轮新发现的缺陷（第 6 个）

第一次在宿主里跑拦截时，hook 返回的中文是**乱码**：

```text
[data-development-plugin:dev_order_guard] （此处原本是整段不可读的乱码字符）
```

根因：`hooks/common.py` 的 `emit()` 用 `json.dumps(obj, ensure_ascii=False)` 直接写 `sys.stdout`，
Windows 下 stdout 编码是 cp936，而宿主按 UTF-8 解析 → 中文整段坏掉。修复：改为 ASCII 转义 JSON
（任意编码下等价，宿主 `json.loads` 还原中文）。修复后同一场景的返回变成可读中文：

```text
[data-development-plugin:dev_order_guard] 工具调用被硬门拦截。dp_task 先于 ETL：写 dp_task/order_daily2.sql 前必须有 etl/order_daily2.sql。
```

这类缺陷只有"在真实宿主里跑一遍"才会暴露——引擎层单测（`python -m unittest`）覆盖不到
宿主与 hook 之间的编码边界。

## 7. 完整流程执行记录（31 步）

| # | 阶段 | 命令 | 结果 |
| --- | --- | --- | --- |
| 1 | P0/P1 | `ddp setup --mode live` | ✅ PASS |
| 2 | P0/P1 | `ddp ticket create --title 【数据需求】订单省域日汇总改造（新增客单价 avg_amount） --description-file <项目根>\ticket_description.md` | ✅ PASS |
| 3 | P0/P1 | `ddp sds validate --ticket T-0001` | ✅ PASS |
| 4 | P0/P1 | `ddp sds validate --confirm --ticket T-0001` | ✅ PASS |
| 5 | P0/P1 | `ddp sds render --ticket T-0001` | ✅ PASS |
| 6 | P0/P1 | `ddp sds build-comment --ticket T-0001` | ✅ PASS |
| 7 | P0/P1 | `ddp comment add --ticket T-0001 --anchor 数据概要设计 v1 --file <项目根>\.data-dev\tickets\T-0001\_sds_comment.md` | ✅ PASS |
| 8 | P0/P1 | `ddp sds bridge --ticket T-0001` | ✅ PASS |
| 9 | P0/P1 | `ddp sds publish --ticket T-0001 --version 1` | ✅ PASS |
| 10 | P1→P2 | `ddp start --ticket T-0001 --autopilot` | ✅ PASS |
| 11 | P2 | `ddp advance --phase 3 --ticket T-0001 --reason P2 出口：架构文档含难度级别与 mermaid 链路图，pipeline.html 无占位符` | ✅ PASS |
| 12 | P3 | `ddp sql check --file <项目根>\.data-dev\tickets\T-0001\ddl\dwd_order_province_di.sql` | ✅ PASS |
| 13 | P3 | `ddp sql check --file <项目根>\.data-dev\tickets\T-0001\etl\dwd_order_province_di.sql` | ✅ PASS |
| 14 | P3 | `ddp sql check --file <项目根>\.data-dev\tickets\T-0001\dp_task\dwd_order_province_di.sql --task` | ✅ PASS |
| 15 | P3 | `ddp platform run-sql --file <项目根>\.data-dev\tickets\T-0001\ddl\dwd_order_province_di.sql --ticket T-0001 --env dev` | ✅ PASS |
| 16 | P3 | `ddp platform run-sql --file <项目根>\.data-dev\tickets\T-0001\etl\dwd_order_province_di.sql --ticket T-0001 --env dev --save-evidence` | ✅ PASS |
| 17 | P3 | `ddp evidence verify --ticket T-0001` | ✅ PASS |
| 18 | P2 | `ddp diff --ticket T-0001` | ✅ PASS |
| 19 | P3 | `ddp evidence selfcheck --ticket T-0001` | ✅ PASS |
| 20 | P3 | `ddp evidence confirm --ticket T-0001` | ✅ PASS |
| 21 | P3 | `ddp platform create-task --name dwd_order_province_di --code-file <项目根>\.data-dev\tickets\T-0001\dp_task\dwd_order_province_di.sql --schedule 0 2 * * *` | ✅ PASS |
| 22 | P3 | `ddp platform submit --name dwd_order_province_di` | ✅ PASS |
| 23 | P2 | `ddp advance --phase 4 --ticket T-0001 --reason P3 出口：ddl/etl/dp_task 齐备 + dev 真跑证据 + self_check + code_diff` | ✅ PASS |
| 24 | P3 | `ddp platform run-sql --file <项目根>\.data-dev\tickets\T-0001\test\dwd_order_province_di_tc_0001.sql --ticket T-0001 --env dev` | ✅ PASS |
| 25 | P2 | `ddp advance --phase 5 --ticket T-0001 --reason P4 出口：test_runs.json 的 query_id 与执行账本一致` | ✅ PASS |
| 26 | P2 | `ddp diff --ticket T-0001` | ✅ PASS |
| 27 | P5 | `ddp review-report --ticket T-0001` | ✅ PASS |
| 28 | P5 | `ddp deploy --ticket T-0001 --comment [已确认发布] AskUserQuestion=yes：发布/ALTER/回刷三道门已逐项确认` | ✅ PASS |
| 29 | P5 | `ddp advance --phase 6 --ticket T-0001 --reason P5 出口：publish_gate token + deploy_log.json` | ✅ PASS |
| 30 | P6 | `ddp semantic --ticket T-0001 --scenario metric-dev` | ✅ PASS |
| 31 | P6 | `ddp finish --ticket T-0001 --reason 从建单到语义层闭环（真实 Doris 端到端）` | ✅ PASS |

执行期间的引擎判定记录：

- Doris 地址 = <本机 Doris 地址>:9030（WSL 内的 FE）
- 已重置 dev/prod 目标表为改造前状态（无 avg_amount 列）
- .data-dev/config.json -> mode=live, platform.kind=doris (dev=ddp_dev, prod=ddp_prod)
- 工单 T-0001 创建成功（8 项质量门通过）
- SDS 证据取自真实 Doris：rows=3, provinces=2, nulls=0
- start 准入门 5 步通过 -> P2（autopilot）
- DDL 已在真实 Doris ddp_dev 执行（ALTER ADD COLUMN avg_amount）
- ETL 已在真实 Doris ddp_dev 执行（INSERT 20260903 分区）
- TC-0001 已在真实 Doris 执行，query_id=run_a69865dae0dbb7

（完整原始日志见附录 A。）

### 7.1 P1 数据概要设计（SDS）

- 证据取自**真实 Doris 数据**：`rows=3 / provinces=2 / nulls=0`，写入 `verification/q1.sql`、`q2.sql` 与 `evidence.json`。
- `sds validate` 跑八条硬规则通过；`_user_confirmation.md` 标记 `[已确认]` 后 `sds validate --confirm` 置 `confirmed=true`。
- 终版贴单：`sds render` → `sds build-comment` → `comment add --anchor "数据概要设计 v1"` → `sds bridge`（合成 `requirement_spec.md`，落 `level=L1` 与 `ticket_hash`）。
- `start` 准入门 5 步通过：工单存在 → 终版锚点存在 → 评论 json 块重跑八规则 → 工单正文 hash 一致 → 上下文重建，随后工单流转"开发中"。

### 7.2 P2 架构设计

产出 `architecture_doc.md`（含难度级别 L1 + mermaid 链路图）与 `pipeline.html`，`advance --phase 3` 触发 P2 出口门通过。

### 7.3 P3 数据研发（真实执行）

- 产出 `ddl/dwd_order_province_di.sql`、`etl/dwd_order_province_di.sql`、`dp_task/dwd_order_province_di.sql`，三条 SQL 各自过 `sql check`（17 项里的零容忍 9 条）。
- **真实执行**（`platform run-sql --save-evidence`）：
  - DDL → `ddp_dev` 真跑 `ALTER TABLE ... ADD COLUMN avg_amount`；
  - ETL → `ddp_dev` 真跑 `INSERT ... SELECT`，回执写进 `etl_dev_exec.json`，`query_id` 来自执行账本。
- `evidence verify`（账本核对）→ `evidence selfcheck`（code_diff 校验）→ `evidence confirm`（用户确认记录）三键齐备。
- `platform create-task` + `submit` 注册周期任务；`advance --phase 4` 触发 `delivery_checklist` + P3 出口门通过。

### 7.4 P4 数据测试

- 用例 `TC-0001`：`20260903` 分区 `order_cnt / total_amount / avg_amount` 与 `ods_order_src` 重算结果一致性（差异行数应为 0）。
- 在真实 Doris 上执行：`query_id=run_a69865dae0dbb7`，`status=SUCCESS`，写入 `test_runs.json` 与 `test_report.md`。
- P4 出口门交叉核对"报告标 ✅ 的 TC ↔ 账本里 SUCCESS 且有 query_id 的执行"后放行。

### 7.5 P5 运维部署（三道不可逆门 + 真实发布）

- `deploy_plan.json`：`env=prod`，两项 `alter` + `backfill`（范围 `20260903~20260903`）。
- 发布门校验 `[已确认发布]` token 后才执行；随后 `deploy` 在 **`ddp_prod`** 真跑：
  - ALTER：`ddp_prod.dwd_order_province_di` 增加 `avg_amount` 列；
  - 回刷：按 `dp_task` 代码 + `${bizdate}` 逐日回刷，写入回刷账本。
- 工单按"待验收 → …"逐级流转（不允许跨 stage），`delivery_summary.md` / `deploy_log.json` 落盘；`advance --phase 6` 通过 P5 出口门。

### 7.6 P6 语义层更新

用户拍板记录（`_user_confirmation.md` + `[已确认]`）后生成 `semantic_update_summary.md`，再 `finish` 收口：P6 出口门通过，工单走到"已交付"，流水线 `finished=true`。

## 8. 真实副作用与证据链

### 8.1 执行账本（`.data-dev/runtime/executions.json`）

| # | query_id | env | status |
| --- | --- | --- | --- |
| 1 | `run_bbb24ec626097f` | dev | SUCCESS |
| 2 | `run_1c397b743731ae` | dev | SUCCESS |
| 3 | `run_a69865dae0dbb7` | dev | SUCCESS |

### 8.2 `ddp_prod` 终态（部署后）

列：`dt, province, order_cnt, total_amount, avg_amount`

| dt | province | order_cnt | total_amount | avg_amount |
| --- | --- | --- | --- | --- |
| 20260903 | 广东 | 1 | 300.00 | 300.00 |
| 20260903 | 浙江 | 2 | 200.00 | 100.00 |

（这两行是 P5 回刷真实写入的，不是 mock。）

### 8.3 流水线状态

```json
{
  "ticket": "T-0001",
  "current_phase": 7,
  "finished": true,
  "level": "L1",
  "run_mode": "autopilot",
  "completed": [
    1,
    2,
    3,
    4,
    5,
    6
  ]
}
```

### 8.4 治理摘要（`governance_report.md` 节选）

```markdown
(missing)
```

## 9. 本轮发现并修复的缺陷

| # | 现象 | 根因 | 修复 | 级别 |
| --- | --- | --- | --- | --- |
| 1 | `ticket create --description-file` 报"需要 --title 与 --description" | `parse_args` 只存连字符键 `description-file`，verb 读 `description_file` | 写入时同时保留原始键与下划线键 | 阻断 |
| 2 | live 模式 P3/P4 出口门恒 FAIL：`dev.exec_evidence.no_ledger_store` | 引擎把账本写 `.data-dev/.runtime/`，校验器读 `.data-dev/runtime/` | 新增 `context.LEDGER_DIR` / `ledger_dir()`，引擎账本统一到 `.data-dev/runtime/`；hook 账本仍留 `.runtime/`，`runtime_guard` 两处都拦 | **阻断（live 模式根本走不完）** |
| 3 | WARN 也能卡死转场 | `Pipeline.advance` 对任何非空门控结果都阻断 | 只有 `FAIL` / `HALTED` 阻断，WARN 只提示 | 高 |
| 4 | 长 SQL 被误判"括号不配对" | 送门控前把 SQL 截断到 200 / 100 字符 | 传全文给门控 | 高 |
| 5 | Windows 下 tracker 的多行评论丢了 `--anchor` | `.cmd` 包装器经 cmd.exe，多行参数被换行切断 | `cli.*` 支持 argv 列表（`["py","-3","script.py"]`），直连解释器 | 中（联网实测发现） |
| 6 | Claude Code 里 hook 拦截原因显示为乱码 | `hooks/common.py` 的 `emit()` 用 `ensure_ascii=False` 写 stdout，Windows 下按 cp936 编码，宿主按 UTF-8 解析 | 改为 ASCII 转义 JSON | 中（只在真实宿主暴露） |

修复后重新生成 `.engine-manifest.json`（`tools/generate_engine_manifest.py`），并同步更新 CHANGELOG 与 `docs/REPRODUCTION-NOTES.md`。

## 10. 回归测试

```text
Ran 111 tests in 734.478s
OK
```

（该回归在 hook 输出编码修复**之后**重跑，确保 hook 层改动没有破坏引擎与门控逻辑。）

全部 111 个单测通过（门禁阻断/放行、状态机、SDS 八规则、SQL 17 项、hook 冒烟、端到端 demo）。

## 11. 已知限制（本轮未改，需产品决策）

1. **`code_diff.required` 没有 CLI 触发点**：只有 demo（进程内）会置位，`ctx set` 又禁止写该键，因此 live 场景下"改现有任务必须 code_diff"这道门实际处于休眠状态。建议由 `platform update-task`（目标任务已存在/已发布）自动置位。
2. **live 模式没有"线上 PUBLISHED 基准"通道**：`diff` 只认 `.data-dev/mock/baseline/<工单>`，文档所述"比对线上基准"在 live 下缺落地路径。
3. **P4 用例文件命名受合规门隐式约束**：测试 SQL 必须能对上目标表名（本次用 `dwd_order_province_di_tc_0001.sql` + `-- table:` 注解才放行），文档未说明。
4. **宿主全流程未跑完**：本轮在 Claude Code 里验证了命令层（`/setup`、`/status`、`/submit`）
   与 Hook 层（含真实会话内的硬门拦截），但没有在一次宿主会话里连续跑完 P1→P6。
   单次 headless 调用会在 tool_use 边界结束，需要多轮续跑（成本按 $0.5–1/轮估算）。
5. 插件经 pymysql 直连拿不到服务端 `query_id`（只能给执行回执）；若要真服务端 query_id，可考虑走 `doriscli` 作为 `kind=cli` 适配器。

## 12. 补充：Codex 宿主全流程实测（P1→P6）

前面第 6 章验证的是"命令层 + Hook 层"的片段；这一节是**在 Codex 里把六阶段连起来跑完**的记录。

执行方式：`codex exec --dangerously-bypass-hook-trust --cd <工程>` 起会话，
后续用 `codex exec resume --last` 续跑（模型 = 宿主的 deepseek 会话，技能 = 插件的 `ddp-*`）。
`--dangerously-bypass-hook-trust` 只是为了在未持久信任的环境里启用 hook（本机 hook 已审查）。

| 项目 | 结果 |
| --- | --- |
| 最终状态 | `current_phase = 7`、`completed = [1, 2, 3, 4, 5, 6]`、`finished = True` |
| 难度 / 模式 | `level = L1`、`run_mode = autopilot` |
| 交付物 | 28 个文件（含 ddl/etl/dp_task、verification 证据链、test_runs.json、deploy_plan/deploy_log、semantic_update_summary.md 等） |
| Hook 调用 | 六次会话累计 **734** 次（`PreToolUse` / `PostToolUse` / `SessionStart` 均实跑） |
| 执行账本 | 7 条（SDS 三查询 + dev DDL + dev ETL + 两个测试用例 + 生产回刷） |

### 12.1 三道不可逆门都真实停在人面前

宿主全流程里，autopilot **没有**越过任何一道不可逆门：

1. P1 收尾前停在**确认门**，要求确认 SDS v1 后才贴单；
2. P5 发布前停在**发布/ALTER/回刷三门**，要求给出 `[已确认发布]` token，并先把 `awaiting_confirmation=true` 锁上（期间交付物写入与生产 CLI 被锁）；
3. P6 前停在**语义层进入门**，给出建议书等用户拍板。

这正是设计意图：**旁路的是可返工的判断，不旁路的是不可逆的风险**——在 Codex 里同样成立。

### 12.2 Hook 在宿主里真实拦截的证据

- 会话日志里可以看到 `hook: PreToolUse` / `PostToolUse` 成对出现，且从最初的 `Failed`（超时）变成 `Completed`（修复超时与 schema 后）。
- 实测被拦下的动作包括：写非法 JSON 的 `sds_design.json`（`sds_json_valid`）、越界写 SQL、以及在把补丁原文当文件内容时的误判（已修）。
- 连**测试者自己**都被拦过一次：命令串里出现 `platform create-task` 字样触发 `setup_guard`；另一次 `Remove-Item` + `.data-dev/engine` 字样触发 `engine_readonly`。这既证明硬门生效，也暴露"关键词匹配会误伤"这一待优化点。

### 12.3 本轮由宿主全流程暴露并修复的 6 个缺陷

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | P1 没有入口 | `state.enter_sds()` 存在但 CLI 未暴露，`advance --phase 1` 被 `unsupported_phase` 挡住 | 新增 `sds enter --mode verify\|generate` |
| 2 | `apply_patch` 写 `sds_design.json` 被判"非法 JSON" | 门控把**整段补丁文本**当文件内容做 JSON 解析 | 新增 `_patch_file_content()`，只取该文件段落并剥掉 `+` 前缀 |
| 3 | Windows 下 `verification/` 阶段门失效 | `str(Path)` 是反斜杠，前缀比较用正斜杠 | 改用 `Path.as_posix()` 归一化 |
| 4 | `sds publish` 被 `submit_guard` / `publish_gate` 锁死 | 引擎的 SDS 贴单与平台发布共用 action token，却要求研发期证据键/发布 token | 两处门控识别 `ddp.py sds publish` 并跳过 |
| 5 | 命令串联污染 `--ticket` 解析 | `_extract_flag` 不切分 `;`/`&&`/`\|\|`，且短参 `-t` 命中 `update-task` | 先按分隔符切段，短参要求前面是空白 |
| 6 | 语义层摘要含与本单无关的假信息 | `verb_semantic` 是写死模板（假模型名 + 假的"OV 已发布"声明） | 改为从 `sds_design.json` 取目标表/字段/知识源，并如实标注"mock 未执行发布" |

> 第 6 条尤其关键：一份带假发布声明的摘要，比缺字段更容易误导验收。

### 12.4 诚实边界

- 本节的宿主全流程跑在 **mock 离线模式**（无真实数据源/OV），`row_count=3 / null_rate=0 / join_match_rate=1.0` 等是 mock 基线值，`query_id` 真实但结果不代表真实统计。
- 真实数据上的口径比对、下游报表回归、OV 可见性抽查仍需在 live 环境另跑一轮（第 7~9 章是 live 引擎层的结果，两者互补）。

## 13. 复现步骤

```bash
# 1) 启动 WSL 里的 Doris（FE+BE）
bash ~/doris/start-doris.sh

# 2) （可选）启动/注册 MCP Server：Codex 侧已注册 [mcp_servers.doris]
~/doris/tools/start-doris-mcp.sh          # stdio 模式，手测可用

# 3) 跑端到端真实流程（会重建 dev/prod 目标表并真实执行 SQL）
cd "<工作区>\ddp-doris-live"
py -3 run_live_flow.py
py -3 verify_final.py                      # 产出 live_flow_report.json

# 4) 插件单测回归
cd "<工作区>\data-development-plugin"
py -3 -m unittest discover -s tests
```

## 附录 A：完整执行日志（原始）

```text
[info] Doris 地址 = <本机 Doris 地址>:9030（WSL 内的 FE）

[info] 已重置 dev/prod 目标表为改造前状态（无 avg_amount 列）

[PASS] ddp setup --mode live

[info] .data-dev/config.json -> mode=live, platform.kind=doris (dev=ddp_dev, prod=ddp_prod)

[PASS] ddp ticket create --title 【数据需求】订单省域日汇总改造（新增客单价 avg_amount） --description-file <工作区>\ddp-doris-live\ticket_description.md

[info] 工单 T-0001 创建成功（8 项质量门通过）

[info] SDS 证据取自真实 Doris：rows=3, provinces=2, nulls=0

[PASS] ddp sds validate --ticket T-0001

[PASS] ddp sds validate --confirm --ticket T-0001

[PASS] ddp sds render --ticket T-0001

[PASS] ddp sds build-comment --ticket T-0001

[PASS] ddp comment add --ticket T-0001 --anchor 数据概要设计 v1 --file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\_sds_comment.md

[PASS] ddp sds bridge --ticket T-0001

[PASS] ddp sds publish --ticket T-0001 --version 1

[PASS] ddp start --ticket T-0001 --autopilot

[info] start 准入门 5 步通过 -> P2（autopilot）

[PASS] ddp advance --phase 3 --ticket T-0001 --reason P2 出口：架构文档含难度级别与 mermaid 链路图，pipeline.html 无占位符

[PASS] ddp sql check --file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\ddl\dwd_order_province_di.sql

[PASS] ddp sql check --file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\etl\dwd_order_province_di.sql

[PASS] ddp sql check --file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\dp_task\dwd_order_province_di.sql --task

[PASS] ddp platform run-sql --file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\ddl\dwd_order_province_di.sql --ticket T-0001 --env dev

[info] DDL 已在真实 Doris ddp_dev 执行（ALTER ADD COLUMN avg_amount）

[PASS] ddp platform run-sql --file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\etl\dwd_order_province_di.sql --ticket T-0001 --env dev --save-evidence

[info] ETL 已在真实 Doris ddp_dev 执行（INSERT 20260903 分区）

[PASS] ddp evidence verify --ticket T-0001

[PASS] ddp diff --ticket T-0001

[PASS] ddp evidence selfcheck --ticket T-0001

[PASS] ddp evidence confirm --ticket T-0001

[PASS] ddp platform create-task --name dwd_order_province_di --code-file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\dp_task\dwd_order_province_di.sql --schedule 0 2 * * *

[PASS] ddp platform submit --name dwd_order_province_di

[PASS] ddp advance --phase 4 --ticket T-0001 --reason P3 出口：ddl/etl/dp_task 齐备 + dev 真跑证据 + self_check + code_diff

[PASS] ddp platform run-sql --file <工作区>\ddp-doris-live\.data-dev\tickets\T-0001\test\dwd_order_province_di_tc_0001.sql --ticket T-0001 --env dev

[info] TC-0001 已在真实 Doris 执行，query_id=run_a69865dae0dbb7

[PASS] ddp advance --phase 5 --ticket T-0001 --reason P4 出口：test_runs.json 的 query_id 与执行账本一致

[PASS] ddp diff --ticket T-0001

[PASS] ddp review-report --ticket T-0001

[PASS] ddp deploy --ticket T-0001 --comment [已确认发布] AskUserQuestion=yes：发布/ALTER/回刷三道门已逐项确认

[PASS] ddp advance --phase 6 --ticket T-0001 --reason P5 出口：publish_gate token + deploy_log.json

[PASS] ddp semantic --ticket T-0001 --scenario metric-dev

[PASS] ddp finish --ticket T-0001 --reason 从建单到语义层闭环（真实 Doris 端到端）



=== 真实 Doris 终态 ===

ddp_prod 列: ['dt', 'province', 'order_cnt', 'total_amount', 'avg_amount']

ddp_prod 20260903 行: [['20260903', '广东', 1, Decimal('300.00'), Decimal('300.00')], ['20260903', '浙江', 2, Decimal('200.00'), Decimal('100.00')]]

最终状态: {"current_phase": 7, "finished": true, "level": "L1", "run_mode": "autopilot", "completed": [1, 2, 3, 4, 5, 6]}



步骤总计 31，失败 0

报告: <工作区>\ddp-doris-live\live_flow_report.json
```

## 附录 B：证据文件清单

| 文件 | 说明 |
| --- | --- |
| `ddp-doris-live/live_run_console.log` | 31 步完整执行日志 |
| `ddp-doris-live/live_flow_report.json` | 终态汇总（状态、账本、prod 数据、测试用例） |
| `ddp-doris-live/evidence/mcp_smoke.txt` | MCP Server 工具清单 + 真实查询 |
| `ddp-doris-live/evidence/doriscli_status.json` | doris-cli 集群状态 |
| `ddp-doris-live/evidence/doriscli_sql.json` | doris-cli 查询（含服务端 query_id） |
| `ddp-doris-live/evidence/readonly_*.txt` | 只读账号权限边界证据 |
| `ddp-doris-live/evidence/env_facts.txt` | 环境事实快照 |
| `ddp-cc-plugin-test/evidence/hook_probe.txt` | Hook 层对抗探针（9 项门控判定） |
| `ddp-cc-plugin-test/evidence/cc_status.txt` | Claude Code `/status` 原始输出 |
| `ddp-cc-plugin-test/evidence/cc_hook_block.txt` | 真实会话内 hook 拦截现场 |
| `ddp-cc-plugin-test/cc_gate_test3.jsonl` | 拦截会话的完整 stream-json 事件流 |
| `<工单>/.data-dev/tickets/T-0001/_state.json` | 状态机终态 |
| `<工单>/.data-dev/tickets/T-0001/governance_report.md` | 治理摘要 |
| `<项目>/.data-dev/runtime/executions.json` | 执行账本（query_id 来源） |
