# data-development-plugin

把「离线数据任务研发工艺」翻译成一台**状态机 + 一组把守每个状态入口/出口的硬门**的插件。
AI 在门内自主干活，门的开关完全由机器证据决定，AI 无法自封通过。

三层防线叠在一起。

```
┌──────────────────────────────────────────────┐
│ 第1层 命令编排  commands/*.md + skills/*      │  AI 的流程剧本
├──────────────────────────────────────────────┤
│ 第2层 状态机    engine/core/state.py          │  _state.json current_phase 0..7
│                前向推进过 stage_gate；回跳需 --allow-back --reason
├──────────────────────────────────────────────┤
│ 第3层 Hook 硬门 hooks/*  + engine/core/gates  │  PreToolUse decision:block 真阻断
│                PostToolUse 验证器软提示 -> 写 context 键 -> 被硬门消费
└──────────────────────────────────────────────┘
```

外部系统全部**连接器化**。任务单（tracker）、数据平台（platform）、质量引擎（quality）、
知识库（knowledge，默认 OpenViking）都在 `.data-dev/config.json` 的 `cli.*` / `knowledge.*`
里配置。mock 模式开箱即用；live 模式可走 OpenViking HTTP、ov CLI，或任何实现 JSON stdout
契约的自定义 CLI。同一个插件同时支持 Claude Code 与 Codex，两边共用一个引擎和一套 hook。

---

## 它解决什么问题

纯取数的活不进这条流水线。只要一个数、或者一张临时对账的明细，走 L0 即席出口，直接产出可执行
的查询就结束，不建表、不留交付物。会上生产的活才进管道，按改动范围定级。

| 档位 | 含义 |
| --- | --- |
| L0 | 纯取数，P1 即席出口，不进研发管道 |
| L1 | 改造快线（改现有任务、加列、改值） |
| L2 | 待明确慢线（涉及中间层或多表） |
| L3 | 新建应用或长链路 |

定级在 P1 给出，P2-P6 据此走不同路径（L1 精简、L2 至少 15 条用例、L3 至少 25 条并带分层前缀等）。
口径不明确按缺陷处理，先问清或缩小范围，禁止含混建单。

### 六阶段

| Phase | 名称 | 主要交付物 | 出口硬门（节选） |
| --- | --- | --- | --- |
| P1 | 数据概要设计 SDS | `sds_design.json` + `verification/` + 工单终版贴单 | 八条硬规则、evidence 比对 HALTED、确认门 |
| P2 | 架构设计 | `architecture_doc.md` + `pipeline.html` | 难度级别 + 链路图 + 无占位符 + 交付表清单 |
| P3 | 数据研发 | `ddl/` + `etl/` + `dp_task/` | SQL 17 项、`etl_dev_exec.json` 真跑证据、self_check、code_diff |
| P4 | 数据测试 | `test_report.md` + `test_runs.json` | 报告 ✅ TC ↔ SUCCESS+query_id 机器比对 |
| P5 | 运维部署 | `delivery_summary.md` + `deploy_log.json` | 三道不可逆确认门 + `[已确认发布]` |
| P6 | 语义层更新 | `semantic_update_summary.md` | 用户拍板 + 模型信息 |

P1 单独站在主流程之外（`/sds-design`）。想清楚和动手做分开，设计结论贴在工单上成为跨会话契约，
准入门以这份终版为唯一锚点。

失败分成三类，各有一层兜底。**顺序失败**由状态机加 dev_order / difficulty / phase_entry 守卫
负责；**证据失败**由 SDS 八规则、`test_runs.json` 机器证据门、SQL 17 项检查负责；
**不可逆失败**由 confirmation_gate 锁写加 publish_gate 的 `[已确认发布]` token 负责，autopilot
也不旁路。

---

## 30 秒上手（不碰任何真实系统）

```bash
git clone <本仓库> && cd data-development-plugin
python engine/ddp.py demo --root /tmp/ddp-demo   # mock 跑通 建单→SDS→P2…P6 闭环（含全部门控）
python -m unittest discover -s tests             # 全量用例（数量以实际输出为准）
python tools/adapt_hooks.py --check              # 非 Windows：hook 启动器必须切成本机 python3
```

三条路径按需要选。

| 你现在想做什么 | 走哪条 |
| --- | --- |
| 只想看看它是什么、流程长什么样 | 上面三行，`demo` 就把整条流水线跑完了 |
| 在自己项目里真用，工具链还没接 | 先按「安装」装好，再 `/setup`；先跑 mock，然后按体检报告逐个补执行器 |
| 接真实平台或开源引擎 | [docs/engines/README.md](docs/engines/README.md) 选引擎 → 填 `.data-dev/config.json` → `platform doctor` 体检 |

---

## 安装

### 环境要求

- Claude Code 与／或 Codex；
- **Python 3.9+**（CI 在 3.9 与 3.12 两端跑全量用例；下限改了要同步改这里，
  `tests/test_repo_invariants.py` 会核对声明与 CI 矩阵一致）。Windows 请确保 `py -3` 可用；
  macOS/Linux 装好后**必须跑一次** `python3 tools/adapt_hooks.py`，不跑的话硬门会静默失效；
- mock 模式开箱即用；live 模式需要 PATH 上有 `tracker` / `quality`，
  知识通道可以改用 HTTP 直连 OpenViking，不必装本地 ov CLI。

### Claude Code

本仓库**本身就是一个合法插件目录**。

```bash
claude plugin validate <本仓库路径>          # √ Validation passed
```

推荐包装成 marketplace 后安装，适合放进团队内部插件仓库。

```bash
python tools/wrap_marketplace.py <目标仓库根>
cd <目标仓库根> && git init && git add . && git commit -m "marketplace: data-development-plugin"
claude plugin marketplace add <目标仓库根>
claude plugin install data-development-plugin
```

也可以直接作为个人 skills 目录插件（`~/.claude/skills/`）或私有 marketplace 的
`plugins/data-development-plugin` 子目录使用。

**切 hook 启动器，非 Windows 必做。** 仓库里的 `hooks/*.json` 默认用 `py -3`，这是 Windows 的
Python 启动器。macOS/Linux 上没有它，hook 命令会直接失败，而**宿主把 hook 失败当成放行**。
表现是插件装好了，十几道硬门一道都不生效，日志里只留一行 `hook: PreToolUse Failed`。

```bash
python3 tools/adapt_hooks.py          # 切成本机启动器
python3 tools/adapt_hooks.py --check  # 体检：启动器不在 PATH 上就返回非零
```

`--check` 是判断「我到底装没装上」的唯一可靠方式，`/setup` 也会提示这一步。

### Codex

Codex 没有斜杠命令，用户入口是**技能**，每个命令都有一份等价入口技能。

```bash
# 1) 生成 Codex 面（.codex-plugin/plugin.json、hooks/claude-codex-hooks.json、
#    skills/ddp-*、engine/templates/AGENTS.ddp.md）
py -3 tools/build_codex_surface.py

# 2) 装进 Codex（本机个人 marketplace：~/.agents/plugins/marketplace.json）
codex plugin add data-development-plugin@personal
```

装好后在数据研发项目里开新会话，用 `data-development-plugin:ddp-*` 调用。

**首次需要一次性信任 hook。** 技能装好即可用，但 Codex 会在首次加载时把插件 hook 登记进
`config.toml` 的 `[hooks.state."data-development-plugin@personal:…"]` 并请求授权。在 Codex 应用里
**新开一个线程**就能看到提示；授权后 PreToolUse 硬门才真正生效。自动化场景可用
`codex exec --dangerously-bypass-hook-trust`，只对已审查来源使用。

两处与 Claude Code 的差异要注意。子代理方面，Claude Code 的 `Task` 在 Codex 里用
`spawn_agent`，或按 `agents/<角色>.md` 的角色定义就地执行；提问方面，`AskUserQuestion`
在 Codex 里直接在对话里问。另外 `skills/ddp-*` 与 `.codex-plugin/plugin.json` 都是生成物，
改命令请改 `commands/*.md` 后重跑 `tools/build_codex_surface.py`，不要手改生成物。

---

## 日常怎么用

一个数据研发项目里，通常这样走一遍。装好并 `/setup` 之后，建单、做概要设计、进流水线。

```text
/setup                      # 初始化 .data-dev、同步引擎、注入规范
/submit 把需求描述给我       # 建单（8 项质量门）
/sds-design T-0001          # P1 数据概要设计（八规则 + 三门 + 终版贴单）
/start T-0001               # 准入门 5 步 -> P2…P6（interactive）
/start T-0001 --autopilot   # autopilot：旁路可返工判断，不可逆门仍要问人
/copilot 把 T-0001 的 ETL 改成按天分区   # 单动作穿插
/resume T-0001              # 跨会话续接
/advance --phase 4 --ticket T-0001 --reason …   # 显式转场
/status T-0001
```

Codex 侧一一对应。

| Claude Code | Codex |
| --- | --- |
| `/setup` | `ddp-setup`（注入 `AGENTS.md` 而不是 `CLAUDE.md`） |
| `/submit <需求>` | `ddp-submit` |
| `/sds-design T-0001` | `ddp-sds-design` |
| `/start T-0001 --autopilot` | `ddp-start` |
| `/status`、`/advance`、`/resume`、`/copilot`、`/overview` | `ddp-status`、`ddp-advance`、`ddp-resume`、`ddp-copilot`、`ddp-overview` |

三种走法覆盖不同大小的活。整套改动走 `/start`；只改一张表、补一个用例、看一眼状态走
`/copilot`；会话断了用 `/resume` 按状态文件接回上一次的进度。

引擎 CLI 也能直接调（Windows `py -3`，POSIX `python3`）。

```bash
py -3 .data-dev/engine/ddp.py --help
py -3 .data-dev/engine/ddp.py status --ticket T-0001
```

---

## 接真实平台

**引擎清单只有一份权威**，在 `engine/core/engines.py`。它登记每个 `platform.kind` 的执行族
（dbapi / command / cli / mcp）、方言、必填配置键、默认端口与**证据级别**。当前覆盖 MySQL 协议族
（mysql/doris/starrocks/tidb/oceanbase）、Hive、Spark SQL、Flink SQL、Trino/Presto、
ClickHouse、PostgreSQL/Greenplum、MaxCompute(ODPS)，湖表格式（Iceberg/Hudi/Paimon/Delta）
是与执行引擎正交的另一个维度。

配之前先跑体检。`platform doctor` 按注册表逐项检查 kind 是否登记、必填键、profiles 形状、
明文口令。**未登记的 kind 一律硬报错并列出可用项**，不再静默落回 CLI 契约。

```bash
py -3 .data-dev/engine/ddp.py platform doctor
```

写 SQL 时按引擎校验。

```bash
py -3 .data-dev/engine/ddp.py sql check --dialect <hive|spark|doris|clickhouse|trino|odps|postgres|flink|mysql>
```

不传 dialect 就按 `platform.kind` 推导，推不出来只跑基线 17 项。Hive 的 UPDATE/DELETE、
Trino 的 INSERT OVERWRITE、MaxCompute 的行级写都是 FAIL；Doris/StarRocks/Spark/PostgreSQL
的条件性写法是 WARN 加修复建议。

建表按引擎出模板。

```bash
py -3 .data-dev/engine/ddp.py ddl render --spec <spec.json> [--dialect <id>] [--out ddl/x.sql]
```

类型映射表（canonical 到每个引擎的物理类型）在 `engine/core/ddl.py`，Doris 的 `DECIMALV3`、
Hive 的 `DECIMAL`、ClickHouse 的 `Decimal`、PostgreSQL 的 `NUMERIC` 不再靠人抄。未知类型或
未知方言直接报错；渲染产物默认**不覆盖**已有 `ddl/` 文件，`--force` 才覆盖；它只是起始模板，
照样要过 `sql check` 与真实执行。

湖表格式通过 `platform.format` 声明（`iceberg`/`hudi`/`paimon`/`delta`/`none`），组合未登记
直接报错。`ddl render --format iceberg` 会出 `USING iceberg` 并附写入与运维提示，
`sql check --format hudi` 会在缺 `primaryKey`、`VACUUM` 这类有条件的写法上出 WARN。
组合矩阵与每条检查见 [docs/engines/README.md](docs/engines/README.md) 的湖表格式一节。
接入通道选型（MCP/CLI/直连）、配置示例与证据级别说明见
[docs/REPRODUCTION-NOTES.md](docs/REPRODUCTION-NOTES.md) 1.2.1 / 1.2.2。

> 兼容性说明：引擎注册表这一轮只**新增可选能力**——方言检查（`--dialect`，不传即旧行为）、
> `platform doctor`（只读体检）、注册表里新增的 kind。老配置（`kind=cli` / `kind=doris` /
> `kind=command` 等）无需改动。唯一的硬行为变化是未登记 kind 从「静默当成 CLI」变成
> 「报错并列出可用项」。

---

## 硬门与兜底

### Hook 矩阵（真阻断 vs 软提醒）

硬阻断走 PreToolUse `decision:block`。`setup_guard`、`phase_entry_guard`、`confirmation_gate`、
`dataprocess_param_guard`、`dataprocess_retry_guard`、`grammar_retry_circuit`、`submit_guard`、
`sql_compliance`、`backfill_guard`、`update_task_read_guard`、`deploy_task_check`、
`publish_gate`、`delivery_checklist`、`dev_order_guard`、`difficulty_guard`、
`deploy_plan_check_hook`、`confirmation_gate_hook`、`submit_quality_gate`、`kb_scope_guard_hook`。

软提醒包括 `stage_gate_hook`、`delivery_write_guard`、PostToolUse 验证器反馈（写 `_state.json`
context 键，被 PreToolUse 硬门消费）、`review_report_gate`（WARN）与 SessionStart 提示。

### autopilot 边界

| 决策点 | interactive | autopilot |
| --- | --- | --- |
| Phase 转场 | 用户确认才进 | stage_gate PASS 自动转场 |
| 入口定级 | L1 双重确认 | AI 基于工具查证自主定级 |
| P2 方案选择 | 至少 3 个方案用户选 | AI 选推荐方案 |
| P4 测试计划 | 强制确认 | 豁免 |
| **P5 发布/ALTER/回刷** | 用户确认 | **不旁路** |
| **P6 是否进语义层** | AI 建议 + 用户拍板 | **不旁路** |

部署前展示 `autopilot_decisions.md` 全文与 `review_report.html`。

### 引擎内兜底（不依赖宿主 hook）

关键动作走引擎时会在引擎内**再跑一遍门**。`platform run-sql` / `create-task` / `update-task` /
`publish` / `backfill` 都先调 `check_gate`；`etl_dev_exec.json` 与 `test_runs.json` 里的
`query_id` 必须能在执行账本里查到。所以「没跑就写证据」「手写 query_id」这两类作弊在两个宿主里
都过不去。Hook 额外保护的是绕过引擎直接改文件的路径，比如用 `apply_patch` 直写 `dp_task/`。

---

## 排障

Codex 侧实机踩过的三条，Claude Code 同样适用。

1. **先确认 hook 真的在跑。** `python3 tools/adapt_hooks.py --check` 返回非零，说明 hooks 文件里的
   启动器（默认 `py -3`）在本机不存在，硬门全部静默失效。跑一次 `tools/adapt_hooks.py` 修掉即可。
   这是唯一一个「装好了但什么都没拦」的失败模式。
2. **改了 `hooks/*.json` 就要重新授权。** Codex 按 hooks 文件内容记信任，改动后旧信任会被清掉，
   表现是 `hook: PreToolUse` 这一行在日志里彻底消失、工具照常执行。新开线程重新点一次即可；
   只重装插件（换 cachebuster）不会使信任失效。
3. **hook 超时会 fail-open。** hook 是「python 启动 + 导入引擎」，机器负载高时能到 20 秒以上
   （裸 `python -c pass` 就要 3.7 秒）。本插件把超时设成 PreToolUse/PostToolUse 60 秒、
   SessionStart 30 秒，并让不相关工具走快速通道直接放行。日志里出现 `hook: PreToolUse Failed`
   且工具仍执行，先按超时排查。

要抓宿主发给 hook 的真实 payload，先设 `$env:DDP_HOOK_TRACE="C:\temp\ddp.jsonl"` 再启动宿主，
两个宿主的原始事件都会落盘（Codex 的补丁文本在 `tool_input.command`）。

---

## 目录结构

```
.claude-plugin/plugin.json    Claude Code 插件清单（claude plugin validate 通过）
.codex-plugin/plugin.json     Codex 插件清单（由 tools/build_codex_surface.py 生成）
commands/                    /setup /submit /sds-design /start /copilot /resume /advance /status /overview
agents/                      sds-designer / data-explorer / architecture-explorer /
                             privilege-applier / data-tester / dqc-deployer / backfiller
skills/                      requirement-analysis / sds-design / architecture-design / data-develop /
                             data-testing / data-deploy / data-backfill / dqc-rules / data-semantic /
                             domain-knowledge / scenario-index / cdm-dev / feature-dev / metric-dev /
                             session-review（两宿主共用）
                             ddp-setup / ddp-submit / ddp-start …（Codex 入口技能，从 commands/ 生成）
hooks/                       hooks.json（Claude Code）+ claude-codex-hooks.json（两宿主共用）
                             PreToolUse 硬门 + PostToolUse 验证器 + SessionStart 提示
workflows/                   phase4-test.js / phase5-deploy.js（schema 强制 query_id / 并行 dqc+回刷）
engine/                      vendor-neutral 核心：ddp.py CLI + core/{context,state,validators,gates,clients,spec}.py
                             templates/CLAUDE.ddp.md、templates/AGENTS.ddp.md、templates/settings.json（setup 注入）
tests/                       单测/端到端（unittest，无宿主依赖；数量以 discover 实际输出为准）
tools/                       wrap_marketplace.py（包装成可安装 marketplace）
                             build_codex_surface.py（从 Claude 源生成 Codex 面）
                             reinstall.py（改完代码后换版本后缀并重装）
                             scan_leaks.py（CI 的脱敏扫描：内网地址与本机路径）
```

---

## 维护插件本身

### 改完代码，怎么让宿主加载新版本

**这一步不做，改的东西一行都不会生效。** Codex 把插件按版本号缓存到
`<codex-home>/plugins/cache/<marketplace>/<plugin>/<version>/`，版本号不变就不会重新拷贝，
所以改完代码必须换一次 `+codex.<token>` 后缀。

```bash
py -3 tools/reinstall.py            # 预演：体检 + 打印将要执行的动作
py -3 tools/reinstall.py --apply    # 换 cachebuster + codex plugin add 重装
```

脚本依次做四件事，任何一步不满足都明确报错而不是装作成功。按 `commands/*.md` 重生成 Codex 面；
确认 marketplace 条目**指向本仓库**（Codex 把 `source.path` 解析成相对**用户主目录**的路径，
不是相对 `marketplace.json` 所在目录，按后者解析会得到一个不存在的路径，本项目踩过这个坑，
本机是目录联接 `~/plugins/data-development-plugin -> 本仓库`）；把版本换成
`<基础版本>+codex.<UTC 时间戳>`（保留 `+` 之前的基础版本）；跑
`codex plugin add <插件名>@<marketplace名>`。

装完**新开一个线程**再用，宿主只在新会话里加载新版本。重装本身不会让 hook 信任失效，
只有改动 `hooks/*.json` 的内容才需要重新授权一次。

> 仓库里为什么自带这个脚本：`tools/build_codex_surface.py` 早先的注释要求版本后缀由
> `update_plugin_cachebuster.py` 维护，但那个脚本属于 Codex 的 plugin-creator 系统 skill，
> 不在本仓库里。clone 下来的人拿不到一条可走的「改完怎么生效」路径。现在的实现逻辑等价，
> 依赖为零。

### 提交前会被拦的东西

CI 除了跑全量用例与生成物一致性检查，还有一条脱敏扫描 `tools/scan_leaks.py`，查内网网段
（10/8、192.168/16、172.16/12）与带用户名的本机绝对路径。写文档时把地址和路径换成
占位符（`<LAN-IP>`、`~`）即可，扫描器会告诉你是哪个文件哪一行。

---

## 文档索引

中文文档为准，[README.en.md](README.en.md) 是简版摘要。

| 你想知道的 | 看这个 |
| --- | --- |
| 状态契约、八条规则 / 17 项检查 / 9 条部署规则细节、上下文键表 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 连接器契约、已知边界与局限 | [docs/REPRODUCTION-NOTES.md](docs/REPRODUCTION-NOTES.md) |
| 引擎接入矩阵与逐引擎 playbook | [docs/engines/README.md](docs/engines/README.md) |
| 使用场景与六阶段运行流程（长文） | [docs/article-usage-scenarios-and-flow.md](docs/article-usage-scenarios-and-flow.md) |
| 项目缘起与设计取舍（长文） | [docs/article-introducing-data-development-plugin.md](docs/article-introducing-data-development-plugin.md) |
| 术语（阶段、出口门、SDS、八条硬规则、机器资产、留证……） | [docs/glossary.md](docs/glossary.md) |
| 按 Apache 标准做的开源就绪审计 | [docs/apache-readiness-audit.md](docs/apache-readiness-audit.md) |
| 怎么参与、怎么跑测试、DCO 怎么签 | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 谁能合并、谁能发版、冲突怎么裁决 | [GOVERNANCE.md](GOVERNANCE.md) |
| 怎么切一个发布（版本单源 → 门禁 → tag） | [RELEASE.md](RELEASE.md) |
| 装不上、门控行为不对、该去哪问 | [SUPPORT.md](SUPPORT.md) |
| 怎么相处、遇到骚扰找谁 | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| 安全漏洞（**不要开公开 issue**） | [SECURITY.md](SECURITY.md) |
| 改动记录 | [CHANGELOG.md](CHANGELOG.md) |

---

## License

Apache License 2.0（`SPDX: Apache-2.0`）。全文见 [LICENSE](LICENSE)，归属与第三方依赖边界见
[NOTICE](NOTICE)。
