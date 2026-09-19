# 设计说明、连接器契约与诚实边界

data-development-plugin 是一套**通用离线数据任务研发流水线**：六阶段状态机 +
PreToolUse/PostToolUse 双层 Hook 门控，让 AI 在门内自主干活，但阶段出口只认机器证据。
本文档记录连接器契约、配置方式与已知边界，避免使用者误以为插件绑定任何特定平台。

## 1. 连接器契约（外部系统全部可配置）

运行时配置在 `.data-dev/config.json`：

```json
{
  "mode": "mock | live",
  "cli": {
    "tracker": "tracker",
    "platform": "platform",
    "quality": "quality",
    "knowledge": "openviking"
  },
  "knowledge": {
    "uri_prefix": "viking://resources/",
    "kind": "auto",          // auto | http | ov-cli | custom-cli
    "ov": { "url": "", "api_key_env": "OV_API_KEY" }
  }
}
```

### 1.1 tracker（任务单 / 需求系统）

live 模式要求 `cli.tracker` 指向一个输出 JSON stdout 的 CLI（非 JSON 会报错，
除非 `live.allow_text=true`），实现：

`create --title --description [--templateTypeName]`、`show --id`、`list`、
`comment --id --content [--anchor]`、`comments --id`、`set-status --id --status [--reason]`。

状态流（不允许跨级）：`已提交 → 开发中 → 待验收 → 待测试 → 待业务验收 → 已交付`。

`cli.tracker`（以及其它 `cli.*`）既可以写可执行文件路径，也可以写 argv 列表，
例如 `"tracker": ["py", "-3", "tools/ddp_tracker.py"]`。Windows 上建议用列表形式：
脚本经 `.cmd` 包装器转发时，cmd.exe 会把多行内容参数按换行切开，后面的
`--anchor` 等参数会整段丢失（实测：多行评论内容 + `.cmd` 包装 → anchor 丢失）。

### 1.2 platform（数据平台 / SQL 执行器，可插拔多数据源）

`platform.kind` 决定走哪一类适配器（都实现同一套
`run-sql / check-grammar / create-task / read-task / update-task / submit /
publish / backfill` 语义）：

| kind | 覆盖数据源 | 执行方式 |
| --- | --- | --- |
| `cli`（默认） | 任意提供 JSON stdout CLI 的平台 | 直接调用 `cli.platform`；输出必须 JSON（`live.allow_text=true` 才容忍文本） |
| `mysql` / `doris` / `starrocks` / `tidb` / `oceanbase` / `generic-sql` | MySQL 协议族 | `pymysql` 直连 `platform.host/port/user/database` |
| `paimon` / `flink` / `spark` / `trino` / `presto` / `command` | 命令执行类引擎 | 每条 SQL 落临时文件，交给 `platform.command.run` 的 argv 模板执行 |

MySQL 协议族示例：

```json
{
  "mode": "live",
  "platform": {
    "kind": "doris",
    "host": "127.0.0.1",
    "port": 9030,
    "user": "root",
    "password_env": "DDP_PLATFORM_PASSWORD",
    "database": "ods"
  }
}
```

Paimon / Flink 示例（把 SQL 交给 flink sql 客户端执行；`{file}` 会被替换成
临时 SQL 文件路径）：

```json
{
  "mode": "live",
  "platform": {
    "kind": "paimon",
    "command": {
      "engine": "flink",
      "run": ["flink", "sql", "-f", "{file}"],
      "check": ["bash", "tools/flink_check.sh", "{file}"]
    }
  }
}
```

接入新引擎时只需新增一个 kind：MySQL 协议引擎零代码接入；命令类引擎
准备一条能执行 SQL 文件的命令即可（Flink/Spark/Trino CLI 或项目自带 bridge
脚本）。任务注册/提交/发布在开源引擎上做的是**本地任务代码快照**
（`.data-dev/runtime/platform_tasks.json`）+ 状态标记；真正把 DDL 落到库、
按日期回刷发生在 P5（见 ARCHITECTURE.md 的 SQL 平台部署分支）。

> 诚实说明：Doris/Paimon 等开源引擎不通过本插件的执行通道暴露服务端
> `query_id`，因此执行证据中的 `query_id` 是适配器生成的执行回执
> （`run_<hash>` + affected_rows + executed_at + duration_s），证明"确实执行过"，
> 不等于平台侧可检索的查询 ID。

### 1.2.3 环境真实隔离、证据新鲜度、可续跑/可回滚

`platform.base_env` 声明顶层 host/database 属于哪个环境；其余环境必须显式写在
`platform.profiles`，否则执行器直接拒绝（防止 dev 部署打到生产库）：

```json
{
  "mode": "live",
  "platform": {
    "kind": "doris",
    "base_env": "dev",
    "host": "dev-doris",
    "database": "ods",
    "profiles": {
      "prod": { "host": "prod-doris", "database": "ods", "password_env": "DDP_PROD_PASSWORD" }
    }
  }
}
```

- 执行证据由引擎产生：`platform run-sql --save-evidence --ticket <id>` 在真实执行
  后由引擎自动写 `etl_dev_exec.json`，并把回执追加到 `.data-dev/runtime/
  executions.json`；`evidence verify` 与 P3/P4 validator 交叉核对 query_id 在账本
  中（AI 手写证据文件会被 evidence_ledger 拦截）。账本文件禁止删除
  （runtime_guard）。`etl_dev_exec.json` 另受三重复核：status=SUCCESS+query_id、
  env∈dev/development/staging、`executed_at` 在 `limits.evidence_max_age_hours`
  （默认 24h）内。
- 回刷（`backfill_code`）逐日写 `.data-dev/runtime/backfill_runs.json`：成功日自动
  跳过，失败日记录后中断；重跑即续跑，不重复已成功分区。
- **live 模式强制账本**：P3/P4 validator 在 `mode=live` 时要求执行账本存在，
  测试/ETL 必须经 `engine platform run-sql` 执行（无账本直接 FAIL，杜绝绕过账本
  手写 query_id）；mock 模式保持宽松。
- 引擎同步自动化：`engine sync --from <插件根>/engine --root <项目>` 替代手动
  copytree，复制后立即做完整性自检。
- 治理摘要：`report [--ticket <id>] [--write]` 输出时间线 + 证据核验 + autopilot
  决策（`--write` 生成 `governance_report.md`），复盘/审计直接消费。
- 安全小项：`platform.password` 明文写在 config.json 时，`status`/`report` 会输出
  `config_warns` 提示改用 `password_env`；回刷账本只保留最近
  `limits.backfill_ledger_max_keys`（默认 20）个范围。
- hooks 跨平台：POSIX 上执行 `python3 tools/adapt_hooks.py` 把 hooks.json 的
  `py -3` 切换为 `python3`（Windows 保持默认，幂等）。
- `deploy_plan` 的 create/alter 项可声明 `rollback_sql_file`；回滚执行：
  `py -3 .data-dev/engine/ddp.py rollback --ticket <id> --comment "[已回滚] ..."`，
  结果写入 `rollback_log.json`。`deploy.require_rollback=true` 时，生产
  create/alter 缺回滚预案直接 FAIL。
- **code_diff 的两个前提在 live 下都补齐了**：
  1) 触发点——`platform update-task`（改现有任务）会自动置位 `code_diff.required`，
     不再只有 demo 能置位；
  2) 基准来源——`baseline capture --ticket <id>` 会从数据平台读回已发布任务代码，
     落到 `.data-dev/runtime/baseline/<工单>/`，`diff` 优先用它（无则回落到
     mock 基准目录）。
- **read 账本不再只靠 hook**：`update_task_read_guard` / `backfill_guard` 依赖的
  "先读后写"记录，现在引擎自己的 `platform read-task` 也会写入
  （`gates.mark_read`），因此在没有宿主 hook 的纯 CLI 场景里同样可用。
- **只读 SQL 的合规口径**：SELECT/WITH 等只读语句不要求 `-- table:` 注解，也不做
  文件名↔目标表比对（用例文件可以叫 `tc_0001.sql`）；INSERT/UPDATE/DDL 等写类语句
  仍然强校验目标表与文件名一致。

### 1.2.1 接入通道选择：MCP / CLI / 引擎直连

接外部数据源不是"二选一"，而是按**谁在调用**选择通道：

| 通道 | 谁发起 | 形态 | 凭证位置 | 适合 |
| --- | --- | --- | --- | --- |
| MCP | Claude Code 会话内的模型 | `mcp__<server>__<tool>` 工具调用 | MCP server 侧 | AI 自主查表/跑 SQL/搜知识；hook 可见、可门控 |
| CLI | 模型经 Bash，或引擎 subprocess | `mysql -e …` / `flink sql -f …` | 命令行参数/环境变量 | 引擎/脚本确定性执行，或平台只提供 CLI |
| 引擎直连 | 引擎进程（不经 AI） | pymysql / JDBC / HTTP | 插件配置/环境变量 | 机器证据与确定性动作（部署、回刷、校验） |

选型原则：**给 AI 交互用 MCP（有 server 时）或 CLI；给门控证据与确定性动作
用引擎直连/CLI 适配器**。同一套六阶段流程与硬门不因通道改变——这也是
`PlatformClient` 接口存在的意义。

CLI 模板除 `{file}` 外还支持内联占位：`{sql}`、`{host}`、`{port}`、`{user}`、
`{database}`，例如用 mysql 客户端连 Doris/MySQL：

```json
{
  "mode": "live",
  "platform": {
    "kind": "command",
    "host": "127.0.0.1",
    "port": 9030,
    "user": "root",
    "database": "ods",
    "command": {
      "engine": "doris",
      "run": ["mysql", "-h", "{host}", "-P", "{port}", "-u", "{user}",
              "-D", "{database}", "-e", "{sql}"]
    }
  }
}
```

> 提示：命令行 `-p{password}` 会出现在进程列表，生产环境建议用 pymysql 直连
> 或数据库客户端支持的环境变量（如 `MYSQL_PWD`）。

### 1.2.2 MCP 预留契约（kind=mcp）

`kind=mcp` 已预留在配置与工厂中：当前引擎 CLI 明确不直调 MCP（调用会得到
指引性报错），等接入具体 MCP server 后由会话内模型调用。

```json
{
  "mode": "live",
  "platform": {
    "kind": "mcp",
    "mcp": {
      "server": "mysql-doris",
      "tools": {
        "run_sql": "run_sql",
        "check_grammar": "check_sql_grammar",
        "create_task": "create_task",
        "read_task": "read_task",
        "update_task": "update_task",
        "submit": "submit",
        "publish": "publish",
        "backfill": "backfill"
      },
      "evidence_required": true
    }
  }
}
```

工具命名约定：`mcp__<server>__<tool>`。接入时的证据契约：`run_sql` /
`publish` / `backfill` 必须返回可解析 JSON `{status, query_id, affected_rows,
duration_s}`（`query_id` 可为空串，但 `status=SUCCESS` 与时间戳必须真实），
PostToolUse 才能据此写门控状态键；不满足 `evidence_required=true` 的调用
不应被当作机器证据。PreToolUse 门控已预留 `mcp__*` 的识别点，完整工具映射
随具体 server 接入时落地。

### 1.3 quality（质量规则）

live 模式要求 `cli.quality` 提供质量规则/告警的查询与部署命令（由 agents 文档给出
结构化 JSON 契约）。mock 模式在 `.data-dev/mock/` 下确定性仿真。

### 1.4 knowledge（知识库 / 元数据召回）

live 模式有三种传输（`knowledge.kind`，默认 `auto`）：

| kind | 通道 | 命令/契约 |
| --- | --- | --- |
| `http` | 引擎直连 OpenViking HTTP | `POST /api/v1/search/find` + `GET /api/v1/content/read` |
| `ov-cli` | 原生 OpenViking CLI | `ov find "<q>" -u <prefix> -L 2 -o json` / `ov read <uri> -o json` |
| `custom-cli` | 自定义知识服务 CLI | 旧契约 `search --query` / `read --uri`（JSON stdout） |

`auto`：配置了 `knowledge.ov.url` 走 `http`，否则走 `ov-cli`。示例：

```jsonc
{
  "mode": "live",
  "knowledge": {
    "uri_prefix": "viking://resources/",
    "kind": "auto",           // auto | http | ov-cli | custom-cli
    "limit": 10,
    "level": 2,
    "ov": {
      "url": "http://<server>:1933",
      "api_key_env": "OV_API_KEY",   // 或 ov.api_key_file 指向本地密钥文件
      "api_key_file": "",
      "account": "",
      "user": ""
    }
  }
}
```

密钥只从环境变量或本地密钥文件读取，不写入 config.json/产物。SDS 的
`knowledge_source_uri` 必须以 `knowledge.uri_prefix`（默认
`viking://resources/`）开头，越界会被 `kb_scope_guard` 与
`LiveKnowledgeClient.read` 双重拦截。接入其他知识服务时替换传输或改前缀即可。

## 2. 为什么工艺看起来是"数仓形状"

六阶段（SDS 设计 → 架构 → 研发 → 测试 → 部署 → 语义/元数据更新）与难度定级
（L0-L3）面向"SQL 离线任务"这一类最常见形态：字段口径要先验证、代码要按 DDL/ETL/
调度任务顺序产出、发布与回刷不可逆。只要数据源支持只读 SELECT + 批式写入/调度，
就能复用整条门控流水线；非 SQL 的执行形态可在 platform adapter 中适配。

### 2.1 流程开关与环境分级（面向通用多数据源）

- `config.modeling.layer_policy`：`enabled`（默认）保留 P1 分层校验与 L3 文件名
  前缀要求；纯 MySQL/Doris/Paimon 等无分层场景设为 `disabled`。
- `config.semantic.enabled`：默认 `true`（metric-dev 必进语义层）；没有语义层/
  元数据目标的项目设为 `false`，P6 只做交付登记。
- `deploy_plan.env`：支持 `dev/test/qa/staging/prod`。只有 `prod` 需要
  `[已确认发布]` token、AskUserQuestion 三道确认门与工单流转；非生产直接执行
  并留证据（不调 tracker 流转）。
- 机器证据键（`verify_result` / `self_check_result` / `user_confirmed` 等）只能由
  `evidence verify|selfcheck|confirm` 写入（分别核对 etl_dev_exec.json /
  code_diff.html / _user_confirmation.md）；`ctx set` 对这些键返回拒绝。
  `_state.json` / `_context.md` / `autopilot_decisions.md` 的工具 Write 被
  `state_write_guard` 常驻拦截。

## 3. 已知边界（诚实记录）

1. **mock/live 契约未在真实平台联调**：tracker/platform CLI 的 JSON 契约是约定，
   接真实系统后字段名需要校准（尤其 create 返回值、评论结构、run_id 字段）。
   上一版"`ctx set` 与 `_state.json` 可自封通过"的后门已通过
   `evidence` 子命令 + `state_write_guard` 关闭；残余面是"能直接 import 引擎写
   内存对象"的进程级对抗，不在工具调用门控范围内（与所有本地工具同理）。
2. **知识库发布委托**：P6 语义层更新会把发布委托给 knowledge-base 类插件；
   本插件侧只留 WARN 记录，不二次拦截发布方内部门控失效。
3. **P5/P6 部分 validator 是 WARN**：deploy_log.json 缺失、质量规则未实配、
   语义层发布留痕等只提醒不阻断；真实约束靠 `publish_gate` 的 `[已确认发布]`
   token 与知识库发布方自身 guard。
4. **Hook 关键词误判噪声**：门控靠命令关键词匹配，`grep publish xxx` 之类只读命令
   可能触发守卫路径（最终按内容放行），无法 100% 消除。
5. **回跳无次数上限、autopilot 决策事后审**：`advance --allow-back` 可反复回跳，
   每次前向仍需过门；autopilot 的决策日志在部署门前才全文展示。
6. **hooks.json 默认 `py -3`（Windows）**：macOS/Linux 用户应改成 `python3`。
7. **引擎副本与机器资产保护**：`.data-dev/engine/**` 禁止工具改写（engine_readonly），
   `.data-dev/runtime/**` 只允许引擎写入；`/setup` 同步引擎时生成
   `.engine-manifest.json` 哈希基线，`ddp.py` 启动会自检，不一致即拒绝执行并提示
   重跑 `/setup`。未初始化（无 `_state.json`）的 ticket 目录拒写
   （ticket_not_initialized）。
8. **Bash 结构边界**：Bash 任意文件写无法被关键词穷尽（如 `python -c` 变体）；
   引擎已拦截常见 rm/mv/写文件形态并对关键资产做完整性检测（detect 型），
   根治需把受信动作收敛到受控命令/MCP（演进路线）。

## 4. 演进顺序建议

1. 接真实 tracker/platform CLI，校准 adapter 字段；
2. 接知识库插件，把 P6 的 WARN 升级为可消费的真实发布记录；
3. 增加回跳熔断与 autopilot 决策前置检查；
4. 用真实业务域的 mock kb 索引替换默认种子；
5. 跑 Claude Code `/plugin eval` 或自建用例目录做回归。
