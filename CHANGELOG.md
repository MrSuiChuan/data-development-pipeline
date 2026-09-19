# Changelog

## [Unreleased]

### Fixed（把"证据"补成"绑定到产物版本的证据"）

对抗式复核发现：引擎已经把 `sql_hash` 写进执行账本，但没有任何出口门读它，
于是"跑过一份 SQL"和"要发布的就是这份 SQL"之间没有约束。实测：P3 出口通过后
把 `etl/` 与 `dp_task/` 的 SQL 整段换成另一个分区口径，P3 出口仍然 FAIL=0 放行。

- **P3/P4 证据绑定产物版本**：新增 `validators.evidence_binds_to_current_sql()`，
  出口门把账本里该 `query_id` 的 `sql_hash` 与当前 `etl/` `dp_task/` 内容比对，
  不一致判 `evidence.binding.stale` FAIL（过门后改代码必须重新真跑）；旧账本缺
  `sql_hash` 时降级 WARN。

### Fixed（补齐后半程的门：数据质量 / 数据回刷）

同一类问题的另外两处复发——证据写下来了、出口门没消费：

- **回刷**：`backfill_runs.json` 原先只有写入方。`validate_phase5_deploy` 现在逐项
  比对 `deploy_plan.json` 的回刷项与账本（key = `表名|start|end`），缺成功记录即
  FAIL；`mode=live` 且适配器不写账本（kind=cli 外部 CLI）时降级 WARN。
  `MockPlatformClient.backfill` 与 `backfill_code` 的无 `bizdate` 单次回刷路径
  一并补上账本写入，避免合法路径被误判。
- **DQC 布防**：`deploy_plan.json` 里每个 `dqc_publish` 项必须在
  `<req_dir>/dqc_results.json` 有 `deployed=true` 的记录，缺即 FAIL。原先 DQC 结果
  只存在于 workflow 返回值与一句自然语言里，既不落盘也不被门消费。
  `workflows/phase5-deploy.js` 的 `next` 与 `agents/dqc-deployer.md` 已同步要求落盘。

### Tests

- 新增 6 个回归用例：P3/P4 证据绑定（一致放行 / 改后拦截）、P5 回刷缺账本、
  回刷范围对不上、DQC 未布防与已布防。

### Fixed（hook 层：修掉"装好了但一道门都不生效"和关键词误伤）

- **非 Windows 装完即静默失效**：`hooks/*.json` 默认用 `py -3`，macOS/Linux 上没有
  这个启动器，hook 命令失败而宿主按 fail-open 放行 → 十几道硬门一道都不生效。
  `tools/adapt_hooks.py` 重写：改为直接改 JSON 里的启动器词（不再靠字符串替换
  `python3 `/`py -3 ` 这种易漏的写法），新增 `--check` 体检模式——启动器不在本机
  PATH 上时返回非零，并打印 `which py / python3 / python` 的实际解析结果。
  README 把"切换启动器"提为安装第 3 步；排障清单加第 0 条（先确认 hook 真的在跑）。
- **CI 现在真的触发 hook**：新增双平台矩阵 job（ubuntu + windows），先 adapt，
  再 `--check`，然后在真 shell 里跑 `tests/test_hook_wiring.py`——它把 `hooks.json`
  里的命令原样取出来、替换 `${CLAUDE_PLUGIN_ROOT}` 后执行，断言"裸调平台 CLI 被拒 /
  只读检索不被误伤 / 无关工具走快速通道"。原 CI 只跑单测，hook 层从来没被真跑过。
- **setup_guard 关键词误伤**：原判定对**整条命令做子串匹配**，命令里出现
  `tracker|platform|quality|openviking` 之一就被当成"未 /setup 裸调平台 CLI"而阻断。
  实测被误拦的都是只读分析：`rg -n "platform" engine/core/sql_platforms.py`、
  `rg -n -i "dqc|quality" ...`、`rg "backfill_runs|platform_tasks" ...`（前一轮评审里
  连续误伤 5 次）。现在新增 `gates.invoked_cli_names()`，只认**段首命令词**，并支持
  `./platform`、`& "C:\tools\quality.exe"`、`FOO=1 sudo tracker`、`echo warmup || platform …`
  等真实调用写法。

### Tests

- 新增 5 个回归用例：段首命令词的 6 种调用写法必须被拦、6 种只读提及必须放行、
  以及 3 个真触发 hook 的接线用例（拒绝 / 放行 / 快速通道）。

### Added（补上"改完代码怎么让宿主加载"这条路）

原先仓库里没有一条可走的重新安装路径：`tools/build_codex_surface.py` 的注释要求版本
后缀由 `update_plugin_cachebuster.py` 维护，但那个脚本属于 Codex 的 plugin-creator
系统 skill，**不在本仓库**。clone 下来的人改完代码，宿主仍然跑旧副本。

- 新增 `tools/reinstall.py`：按 `commands/` 重生成 Codex 面 → 校验 marketplace 条目
  是否**指向本仓库** → 把版本换成 `<基础版本>+codex.<UTC 时间戳>` → 跑
  `codex plugin add <插件>@<marketplace>`。默认只预演，`--apply` 才动手；每一步
  不满足都明确报错而不是装作成功。
- 记录一个容易踩的坑：Codex 把 marketplace 的 `source.path` 解析成相对**用户主目录**
  的路径，不是相对 `marketplace.json` 所在目录。按后者解析得到的是
  `.agents/plugins/plugins/...`——那个路径并不存在。本机真实布局是目录联接
  `~/plugins/data-development-plugin -> 本仓库`。
- README 新增「改完插件代码，怎么让宿主加载新版本」一节，写明"重装不影响 hook 信任、
  但要新开线程"。
- 新增 2 个用例锁定 cachebuster 拼接与路径解析口径（这两处错了会静默装一个旧副本）。

### Added（给生成物加同步门）

`tools/build_codex_surface.py` 从 `commands/*.md` 生成 12 个文件（Codex 清单、共用
hooks、9 个 `ddp-*` 入口技能、AGENTS 模板），但 tests 和 CI 里没有任何一处核对它们
同步。改完 command 忘了重跑生成器，两个宿主就会跑出不同的流程，而且不报错。

本次任务里它已经漂过一次：仓库里的 `.codex-plugin/plugin.json` 是 `\uXXXX` 转义，
生成器输出的是 UTF-8 原文——内容等价但字节不同，说明那份清单不是这个生成器写出来的。

- 新增 `tests/test_generated_surface.py`：把生成器输出**截在内存里**（替换 `write`，
  不落盘），与仓库文件逐字节比对，不一致即 fail 并打印是哪几个文件、差多少字符；
  另一个用例断言生成器仍然覆盖全部 12 个文件，防止"少生成几个"也能通过比对。
- 顺带重跑一次生成器，消掉上面那处转义差异。现在 12/12 一致。
- 已做正对照：人为往一个生成物里加一行，测试按预期失败并给出精确文件名，还原后恢复通过。
- CI 主 job 跑 `unittest discover`，自动覆盖本测试。

### Fixed（版本号收敛到单一来源 + 引擎清单进 CI）

**DEVEX-002**：基础版本原先写在四个地方——`tools/build_codex_surface.py` 的
`VERSION` 常量、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、
`CHANGELOG.md`——没有任何检查。加了 `tools/reinstall.py` 之后更危险：它只改
`.codex-plugin` 的版本，另外三处不会跟着动。

- 生成器不再硬编码版本：`build_codex_surface.py` 改从 `.claude-plugin/plugin.json`
  读基础版本（读不到就明确报错），其余位置由它派生。`VERSION` 常量已删除。
- `tests/test_repo_invariants.py` 断言三者一致：Claude 清单版本 == Codex 清单里
  `+` 之前的基础版本 == CHANGELOG **最新已发布**小节（跳过 `[Unreleased]`，
  这点不写清会误报）。另加一条：`+` 后面只允许 `codex.` 前缀的 cachebuster。

**DEVEX-003**：`engine/.engine-manifest.json` 有人生成、没人核对。CONTRIBUTING 要求
改完 engine/ 必须重跑，但 CI 不检查——忘了重跑，每个贡献者 `/setup` 之后的
engine_integrity 自检都会失败，错误暴露在用户侧而不是 CI。

- `tools/generate_engine_manifest.py` 拆出纯函数 `build_manifest()`，`main()` 只负责写盘。
- 同一测试文件里比对清单与 engine/ 当前内容，能分别报出"清单里有但文件没了 /
  文件有但清单没登记 / 内容变了没更新"。

两个检查都**只读不写**——写成"跑生成器再比对"会自己把漂移修好，然后永远通过。
已做正对照：把 `.claude-plugin` 版本改成 9.9.9、往 `engine/core/spec.py` 追加一行，
两条检查都按预期失败并指名问题，还原后恢复通过。

### Fixed（证据保鲜期贯穿全链路，P5 补上绑定）

**PIPE-003**：P3 出口走的是完整的 `execution_evidence_ok`（status + 非空 query_id +
env + `executed_at` 时效 + 账本命中），而 P4 的前置检查只有一行
`exec_ev.get("status") != "SUCCESS"`，P5/P6 完全不看。结果是：一份 24 小时有效期的
证据，只要在 P3 通过的那一刻还没过期，之后搁置多久都能一路放行到发布。

- P4 前置改调 `execution_evidence_ok`，时效与账本命中一并复验；`query_id` 不在账本里
  单独判 `test.precondition.etl_ledger`。
- **P5 补上绑定**：原先 P5 只查 `deploy_plan` / `delivery_summary`，不碰 ETL 证据——
  于是过 P4 之后又改 ETL 照样能部署。现在 P5 也会把账本指纹与当前
  `etl/` `dp_task/` 内容比对，不一致即 FAIL。
- **P5 刻意不重复判时效**，判断依据写进了 `docs/ARCHITECTURE.md` 的阶段处理表：
  时效回答"dev 什么时候跑的"（P4 已判），绑定回答"要部署的是不是被测过的那一份"
  （P5 必须仍然成立）。让 P5 也判时效会把跨天推进的正常工单拦掉，而它要部署的代码
  已经被 P4 验证过。

### Tests

- 新增 2 个用例：证据超过 30 小时（跨过保鲜期）时 P4 必须 FAIL；P4 之后改 ETL 时
  P5 必须 FAIL（改之前 P5 不判绑定，改动确实拦不住）。

### Fixed（引擎清单对行尾免疫——"别人 clone 下来"不再当场报错）

验证"能不能被他人 clone"时实测到的真 bug：**新克隆的 `engine_integrity` 全线不一致**。

```
工作副本: engine/core/validators.py CRLF=745 / 总行数=1165
新克隆  : engine/core/validators.py CRLF=0   / 总行数=1165
新克隆 engine_integrity: 不一致 -> core/clients.py, core/context.py,
                                  core/gates.py, core/spec.py, core/validators.py
```

根因：`.engine-manifest.json` 存的是文件**原始字节**的 sha256，而文件的字节受 git
行尾规范化影响。本机 `core.autocrlf=true` 得到 CRLF，新克隆按 `.gitattributes`
（`eol=lf`）得到 LF，同一份仓库字节不同 → 清单必然对不上。这个仓库最不能出的一类
问题：**声明与事实脱节**，而且错误暴露在别人那里。

- `context.sha256_file()` 改为**行尾先归一为 LF 再哈希**（引擎文件最大 68KB，整读即可；
  分块读会有 `\r\n` 被切在块边界上的风险）。
- `tools/generate_engine_manifest.py` 删掉自己那份内联字节哈希，改为调用
  `context.sha256_file`——生成器和自检必须用同一个函数，否则跨平台必然对不上。
- 新增 2 个用例：同一段内容写成 LF 与 CRLF 必须得到同一个哈希；生成器的输出必须等于
  `context.sha256_file` 的结果。
- 修改 `.gitattributes` 的注释，把"因此必须按归一化后的内容哈希"写清楚，避免以后有人
  把哈希改回字节比较。

## [1.2.2] — 2026-09-11

### Added
- **双宿主：同一个插件现在也能在 Codex 里跑**。
  - `.codex-plugin/plugin.json`（Codex 清单）+ `hooks/claude-codex-hooks.json`
    （两宿主共用 hook 配置，Codex 与 Claude Code 共用 `PreToolUse` /
    `PostToolUse` / `SessionStart` 契约与 `CLAUDE_PLUGIN_ROOT` 注入）。
  - `skills/ddp-*`：Codex 没有斜杠命令，用户入口是技能，因此每个命令都有一份
    等价入口技能（`/setup` → `ddp-setup`，`/start` → `ddp-start` …）。
  - `tools/build_codex_surface.py`：从 `commands/*.md` 生成 Codex 面（清单、hooks、
    入口技能、`engine/templates/AGENTS.ddp.md`），生成物带「请勿手改」抬头。
  - `engine/templates/AGENTS.ddp.md`：Codex 读 `AGENTS.md`，`/setup` 按宿主
    （`PLUGIN_DATA` 存在即 Codex）注入对应文件名，Codex 跳过 `.claude/settings.json`。
- `tools/adapt_hooks.py` 现在同时适配 `hooks/hooks.json` 与
  `hooks/claude-codex-hooks.json`（POSIX 切 `python3`，Windows 保持 `py -3`）。

### Notes
- 安装方式（本机个人 marketplace）：`codex plugin add data-development-plugin@personal`。
- 宿主差异：Claude Code 的 `Task` 在 Codex 用 `spawn_agent`；
  `AskUserQuestion` 改为对话内直接问。引擎与 hook 层完全共用，硬门语义不变。
- **Codex 侧 hook 需要一次性信任**：技能装好即可用，但宿主会在首次加载时把插件的
  hook 登记到 `config.toml` 的 `[hooks.state."data-development-plugin@personal:…"]`
  并请求授权（在 Codex 应用里新开线程可见）。自动化可用
  `codex exec --dangerously-bypass-hook-trust`。
- `engine/core/gates.py` 的 PreToolUse 分派新增 Codex 工具名支持：
  `apply_patch`（从 `*** Add File:` / `*** Update File:` / `+++ b/` 里抽取目标路径后走同一套
  `decide_file_write`）与 `exec_command` / `local_shell` / `shell_command`（命令类走
  `decide_cli_command`）；新增 5 个 Codex payload 单测。
- 引擎内兜底：不依赖宿主 hook 的关键门控（`platform *` 动词内先 `check_gate`、
  证据 query_id 必须命中执行账本）在两个宿主里都生效，因此"没跑就写证据/手写 query_id"
  这类作弊在 Codex 里同样过不去。

### Fixed（Codex 实机联调第二轮）
- **hook 输出必须严格贴宿主 schema**：Codex 的 `pre-tool-use.command.output` 是
  `additionalProperties: false`，我们在 `hookSpecificOutput` 里多写的
  `decision` / `shouldBlock` / `permissionDecisionDetails` 会让宿主**整条判为非法并静默放行**。
  现在只输出 schema 允许的键（顶层 `decision` + `reason` + `hookSpecificOutput`），
  并改用 exit 0 + JSON 决策（exit 2 只对"把原因写 stderr"的旧协议有效）。
  `sessionstart.py` 同时补上了缺失的 `hookEventName`。
- **hook 超时会导致 fail-open**：Windows 上"python 启动 + 导入整个引擎"实测约 20s
  （裸 `python -c pass` 就要 3.7s，机器负载高时更久），而 hook 超时是 20s/10s →
  超时即放行。超时调整为 PreToolUse/PostToolUse 60s、SessionStart 30s；
  同时 PreToolUse 增加快速通道：只有 Bash/Write/Edit/apply_patch/exec_command 与
  MCP 类工具才惰性导入引擎，其余工具直接放行。
- **Codex `apply_patch` payload 形状**：补丁文本在 `tool_input["command"]`（不是
  `input`/`patch`），且路径是相对路径；门控现在按 payload 的 `cwd` 解析后再判
  `workspace_guard` / `runtime_guard` / `engine_readonly` / `dev_order_guard`。
  新增 2 个用**真实抓取 payload**做的回归测试，并加了"hook 输出键集合不得超出 schema"的测试。

### Notes（Codex hook 信任）
- 修改插件的 hooks 文件（如调超时）会让 Codex 里已登记的信任**失效**——需要在
  Codex 应用里新开线程重新授权；仅重装插件（cachebuster）不会失效。
- 排查用：`DDP_HOOK_TRACE=<file>` 环境变量可把两个宿主发给 hook 的原始 payload 落盘，
  便于对齐宿主差异。

### Added / Fixed（按实机联调建议落地）
- **`code_diff.required` 有了真实触发点**：`platform update-task`（改现有任务）会自动置位，
  不再只有 demo（进程内）能置位——live 场景下"改现有任务必须比对线上基准"这道门不再休眠。
- **live 模式补上"线上基准"通道**：新增 `baseline capture|show`，从数据平台读回当前已发布
  任务代码写入 `.data-dev/runtime/baseline/<工单>/`，`diff` 优先用它比对
  （mock 模式的 `.data-dev/mock/baseline` 保留）。
- **修复纯 CLI 场景的死锁**：`update_task_read_guard` 依赖的 read 账本原先只由 PostToolUse
  hook 写入，没有 hook 的宿主里 `update-task` 永远过不去；现在引擎自己的
  `platform read-task` 也会记读（`gates.mark_read`）。
- **只读 SQL 不再被"目标表"规则误杀**：SELECT/WITH 等只读语句不再强制要求 `-- table:`
  注解、也不再拿文件名比对目标表（P4 用例 `tc_0001.sql` 这类命名从此合法）；写类 SQL
  规则保持不变。
- **SessionStart 提示修正**：版本号改为从插件清单动态读取（原来写死 v1.1.0），提示里的
  命令名按宿主显示（Codex 显示 `ddp-setup`/`ddp-submit`，Claude Code 显示 `/setup`/`/submit`）。

### Fixed（Codex 宿主全流程 P1→P6 实测暴露，第 6 轮）

在 Codex 里用 `ddp-*` 技能连跑完整六阶段（mock 模式）时暴露并修复：

- **P1 缺 CLI 入口**：`state.enter_sds()` 早已存在但没有任何调用方，
  `advance --phase 1` 又被 `unsupported_phase` 挡住。新增 `sds enter --mode verify|generate`。
- **`apply_patch` 内容被误判**：门控把**整段补丁文本**当文件内容做 JSON 校验，
  写 `sds_design.json` 必然报"不是合法 JSON"。新增 `_patch_file_content()`，
  只取该文件段落并剥掉 `+` 前缀。
- **Windows 下 `verification/` 阶段门失效**：`str(Path)` 是反斜杠，前缀比较用正斜杠 →
  归一化为 `Path.as_posix()`。
- **`sds publish` 被锁死**：引擎的 P1 终版贴单与平台发布共用 action token，
  却被要求提供研发期证据键与 `[已确认发布]` token。`submit_guard` 与 `publish_gate`
  现在识别 `ddp.py sds publish` 并跳过。
- **命令串联污染 `--ticket`**：`_extract_flag` 不切分 `;`/`&&`/`||`，短参 `-t` 还会命中
  `update-task`（实测把 ticket 解析成 "ask"）。现在按分隔符切段，且短参要求前面是空白。
- **语义层摘要含假信息**：`verb_semantic` 是写死模板，带与本单无关的模型名和
  "kb-publish: [已发布] OV 可见"。改为从 `sds_design.json` 取目标表/字段/知识源，
  并如实标注 mock 未执行发布、真实可见性以 knowledge-base 插件产物为准。

### Fixed（Codex 宿主复测暴露：shell 写入绕过）

- **Bash 改写机器资产的门控被 PowerShell 绕过**：原有"敏感路径 + 变更动词"检查只认
  `rm/mv/write_text` 一类，实测模型用 `New-Item` + `Set-Content` 直接写
  `.data-dev/runtime/` 成功落盘（先被 apply_patch 拦、再用 shell 绕过）。
  现补齐写文件动词表：`set-content` / `add-content` / `out-file` / `new-item` /
  `clear-content` / `copy-item` / `cp` / `tee` / `touch` / `dd` / `sed -i`，以及
  shell 重定向 `>` `>>`；同时把"shell 写工单工作区外的 `ddl|etl|dp_task`"
  也纳入 `workspace_guard`。读取类命令（`Get-Content` / `Test-Path`）保持放行。
- 复测方式与结论：在 Codex 里明确要求"用 PowerShell 而非 apply_patch"写
  `.data-dev/runtime/retest2.json` → 宿主返回
  `Command blocked by PreToolUse hook: [data-development-plugin:runtime_guard] …`，
  文件未落盘，日志出现 `hook: PreToolUse Blocked`。

> 宿主 hook 状态词的含义（实测）：`Completed` = 放行，`Failed` = hook 自身出错/超时，
> `Blocked` = 决策为 deny 且宿主采纳。排查时先看是 Blocked 还是 Failed。

### Fixed（开源前对抗式验收：第一性原理逐条证伪）

用一套 33 条的对抗脚本（路径变形 / 工具变形 / shell 变形 / 证据伪造）去打硬门，
发现并修复：

- **`apply_patch` 的 `*** Move to:` 目标路径未被检查**：可用
  `Update File: ok.txt` + `Move to: .data-dev/runtime/evil.json` 把文件"搬进"受保护目录；
  反向也能把 `.data-dev/engine/ddp.py` 搬走。现在 Move 的源路径与目标路径都纳入门控
  （`runtime_guard` / `engine_readonly`）。
- 同批验证通过的既有防线：绝对/相对/反斜杠/`./`/`../`/大写/尾随空格等路径变形、
  `Set-Content`/`Out-File`/`New-Item`/`cp`/`mv`/`tee`/`sed -i`/`python -c open(...,'w')`
  等写文件方式、shell 重定向、工作区外 SQL 目录写入，全部 BLOCK；
  只读命令（`Get-Content`/`Test-Path`/`Get-ChildItem`）与常规命令保持 ALLOW；
  在 live 模式下**手写 `etl_dev_exec.json` 的 query_id 会被执行账本交叉核对拒绝**。

### Changed（开源前清理）

- **版本号三处对齐到 1.2.2**：`.claude-plugin/plugin.json` 之前落后在 1.1.0，
  Codex 清单与 CHANGELOG 也不一致；生成器现在只保留 `+codex.<token>` 后缀，
  基础版本统一由 `tools/build_codex_surface.py` 的 `VERSION` 决定。
- **测试报告脱敏**：`docs/TEST-REPORT-doris-live.md` 里的本机用户名、家目录、
  内网地址全部替换为占位符（生成器里加了 `sanitize()`，重跑不会回潮）。
- README 里过时的测试数量（27 / 85）更新为当前的 113。
- 跨平台验证：同一套测试在 **Linux（WSL, Python 3.13）** 下 `demo` 成功、
  113 个单测全绿；Windows 侧同样 113 全绿。

### Fixed（对抗式验收 v2：路径归一化与载荷形状）

第二轮对抗从"宿主会怎么把路径喂进来"下手，又打出一类真实绕过：

- **Windows 路径归一化怪癖**：文件系统会忽略路径分量上的**尾随点与空格**
  （`runtime.` 就是 `runtime`、`_state.json.` 就是 `_state.json`），
  因此 `.data-dev/runtime./x.json`、`.data-dev/tickets/T-0001/_state.json.` 这类
  等价写法能绕过 `runtime_guard` / `state_write_guard`。
  现在对每个路径分量做 `rstrip(" .")` 归一化，再参与比较。
- **载荷形状兜底**：工具名缺失/没见过、或 `tool_input` 不是对象（列表/字符串）时，
  具名分支取不到 `file_path` 会直接放行。现在加了**形状兜底**：
  只要载荷里出现"写哪个文件/跑什么命令"的形状就照常过门；形状异常却提到机器资产时
  保守拦截（这是唯一一处 fail-closed）。
- 新增 3 个回归用例（Windows 尾随点/空格、形状兜底），单测 113 → **116**，
  对抗脚本 v2 **28/28**、v1 **33/33**。

### Fixed（关键词误判：按段匹配）

- 原先"命令里同时出现机器资产路径与写文件动词就拦"是**整条命令**匹配，
  跨行的分析脚本会被误伤（实测被误拦过一次：一行 `shutil`、另一行提到 `_state.json`）。
  现在按 `;` / `&&` / `||` / 换行**切段**，要求写动词与敏感路径落在**同一段**才判违规——
  真实写入命令天然同段，分析脚本不再误伤。新增 2 个回归用例（同段拦截 / 跨段放行），
  单测 116 → **118**。

### Fixed（按段匹配的漏网：变量间接与 cd）

第一版"按段匹配"改完，宿主实测立刻打回一个**自己引入的回归**：

```powershell
$t = Join-Path $PWD '.data-dev\runtime\final-check.json'   # 路径在第 1 段
Set-Content -Path $t -Value '{}'                            # 写命令在第 2 段，只出现变量名
```

两段分开看都"无辜"，于是被放行。补两条规则：

- **变量展开**：把同一条命令里的简单赋值（`$t = ...`）展开到后续段，再做同段判断；
  展开时用 lambda 替换，避免 Windows 路径里的反斜杠被当成正则转义。
- **`cd` 进机器资产目录**：`cd .data-dev/runtime` 之后的任何写操作（相对路径）一律拦，
  因为段里看不到绝对路径但意图明确。

宿主复验（Codex 实机）：无害的多行分析命令 `hook: PreToolUse Completed`（放行），
上面那串变量间接写入 `hook: PreToolUse Blocked` + `runtime_guard`，文件未落盘。
单测 118 → **120**。

### Docs

- `SECURITY.md` 增补「Security model」与「Known limits / non-goals」：
  明确三层防线强度不同（hook = 提高作弊成本、引擎内门控 = 绕不开、执行账本 = 证据可信度的真正来源），
  并如实列出边界（启发式命令判定不是 shell 解释器、宿主看不到的子进程副作用、
  live 适配器只给执行回执、不是沙箱/RBAC），附生产使用建议。

## [1.2.1] — 2026-09-10

真实 Apache Doris 端到端联调（live 模式：tracker = JSON CLI，platform = kind=doris，
dev/prod 双库）暴露并修复的缺陷：

### Fixed
- `ticket create --description-file` 实际不可用：`parse_args` 只把 `--description-file`
  存成连字符键，而 verb 读的是 `description_file`。现在两种键名都写入，`--save-evidence`
  一类连字符读取保持兼容。
- **live 模式账本路径不一致（阻断级）**：引擎把执行账本写到 `.data-dev/.runtime/`，
  而 P3/P4 校验器读 `.data-dev/runtime/`，导致 live 模式出口门恒 FAIL
  （`dev.exec_evidence.no_ledger_store`）。新增 `context.LEDGER_DIR` /
  `context.ledger_dir()`，引擎账本（executions / platform_tasks / backfill_runs /
  sql_runs）统一落在 `.data-dev/runtime/`；hook 账本仍在 `.data-dev/.runtime/`，
  `runtime_guard` 同时拦两处。
- `Pipeline.advance` 把任何非空门控结果都当阻断，WARN（如"新表场景无线上基准"）
  也会卡死转场。现在只有 `FAIL` / `HALTED` 阻断。
- `platform run-sql` / `create-task` / `update-task` 在送门控前把 SQL 截断到
  200/100 字符，长 SQL 必然被误判"括号不配对"并拒绝执行。改为传全文。
- **hook 输出在 Windows 上整段乱码**：`hooks/common.py` 的 `emit()` 用
  `ensure_ascii=False` 直接写 stdout，Windows 下按 cp936 落字节，而 Claude Code
  按 UTF-8 解析 → 拦截原因显示成乱码（实测 `dev_order_guard` 的拒绝信息）。
  改为 ASCII 转义 JSON，宿主 `json.loads` 后还原中文，且与终端编码无关。

### Added
- `cli.<name>` 接受 argv 列表（如 `["py", "-3", "tools/tracker.py"]`）：Windows 上
  脚本类 CLI 若经 `.cmd` 包装器转发，多行内容参数会被 cmd.exe 按换行切断，
  后续参数（例如 `--anchor`）整段丢失；列表形式直接调用解释器可绕开。

## [1.2.0] — 2026-09-10

### Added
- Live knowledge retrieval now speaks the real OpenViking contract:
  `POST /api/v1/search/find` (HTTP direct) or native `ov find … -o json` /
  `ov read … -o json` (CLI). Results are normalized to
  `{uri, domain, layer, abstract, score, level}`.
- `engine/core/ov_knowledge.py` pure-stdlib HTTP client with
  `knowledge.ov.url` / `OV_API_KEY` (env or key file), account/user headers,
  timeout, level and limit controls.
- `knowledge.kind`: `auto` (default; URL set → HTTP, otherwise ov-CLI),
  `http`, `ov-cli`, `custom-cli` (keeps the legacy `search --query` /
  `read --uri` wrapper contract).
- `MockKnowledgeClient` honors configured `knowledge.uri_prefix`, and
  `LiveKnowledgeClient.read` enforces the same prefix before any network call.

## [1.1.0] — 2026-09-10

### Added
- Pluggable SQL data-source adapters: MySQL protocol family
  (mysql/doris/starrocks/tidb/oceanbase), command family
  (paimon/flink/spark/trino/presto), reserved `kind=mcp`.
- Environment profiles (`platform.base_env` + `platform.profiles.<env>`);
  deploy env grading (`dev|test|qa|staging|prod`) with prod-only confirmation.
- `modeling.layer_policy` and `semantic.enabled` flow switches.
- `evidence verify|selfcheck|confirm`, execution ledger
  (`.data-dev/runtime/executions.json`) with cross-checks.
- Backfill resume ledger (`backfill_runs.json`), rollback support
  (`rollback_sql_file` + `ddp.py rollback`).
- `engine sync`, `status --gate`, `report` governance summary.
- Integrity hardening: `state_write_guard`, `runtime_guard`,
  `engine_readonly`, `engine_integrity` manifest self-check.
- `tools/adapt_hooks.py` for POSIX hook launcher.

### Changed
- Renamed/removed vendor branding; connectors are config-driven.
- Strict JSON contract for live CLI (opt out via `live.allow_text=true`).

### Fixed
- Raw `ctx set` no longer writes machine evidence keys.
- `_state.json`/engine/runtime files cannot be modified through tool calls.
