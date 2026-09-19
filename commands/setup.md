---
description: 初始化 data-development-plugin：建立 .data-dev 运行时、同步引擎、注入编码规范与权限白名单
argument-hint: [--mode mock|live]
allowed-tools: [Bash, Read, Write, Edit, Glob]
---

# /setup — data-development-plugin 项目初始化

第一次使用必须执行本命令。目标：把插件引擎安装到当前项目 `.data-dev/engine/`，
创建运行时配置（mock 离线仿真 / live 真实 CLI），并注入研发编码规范。

## 步骤

1. **确认当前目录是数据研发项目根目录**（不要在插件仓库里执行）。

2. **定位插件根目录**（含 `engine/` 与 `.claude-plugin/plugin.json` 的目录）：

   - 先运行 `echo $env:CLAUDE_PLUGIN_ROOT`（PowerShell）或 `echo ${CLAUDE_PLUGIN_ROOT}`（sh）。
     若不为空且存在，直接使用。
   - 否则在常见位置查找：
     `~/.claude/plugins/cache/**/data-development-plugin`、
     `~/.claude/plugins/marketplaces/*/plugins/data-development-plugin`，
     `~/.codex/plugins/cache/**/data-development-plugin`、
     `~/.plugins/data-development-plugin`（Codex 个人 marketplace 默认位置），
     或让用户提供插件路径。
   - 也可以把本命令的执行目录放在插件仓库根，此时 `.claude-plugin/plugin.json` 即在当前目录。

3. **同步引擎**（把 `<插件根>/engine` 复制到 `.data-dev/engine` 并写哈希基线）：

   - Windows: `py -3 "<插件根>/engine/ddp.py" engine sync --from "<插件根>/engine" --root <当前项目根>`
   - macOS/Linux: `python3 "<插件根>/engine/ddp.py" engine sync --from "<插件根>/engine" --root <当前项目根>`
   - 引擎同步后自带 `.engine-manifest.json`，后续每次 `ddp.py` 启动会做完整性自检。

4. **初始化运行时**：

   - Windows: `py -3 .data-dev/engine/ddp.py setup --mode mock`（或 `--mode live` 接真实 CLI）
   - macOS/Linux: `python3 .data-dev/engine/ddp.py setup --mode mock`

5. **注入编码规范**（按宿主选文件名）：

   - **Codex**（环境变量 `PLUGIN_DATA` 存在即为 Codex）：读取
     `<插件根>/engine/templates/AGENTS.ddp.md`，写入项目根 `AGENTS.md`
     （已存在则只在缺失标记时追加一次）。Codex 不读 `.claude/settings.json`，跳过权限白名单。
   - **Claude Code**：读取 `<插件根>/engine/templates/CLAUDE.ddp.md`，
     若项目根没有 `CLAUDE.md` 则整文件写入；已有则在文件尾追加（只在缺失标记时追加一次，
     不要重复注入）。同时把 `engine/templates/settings.json` 的权限白名单合并到
     `.claude/settings.json`（不要覆盖用户已有的其他配置项）。

6. **收尾**：运行 `py -3 .data-dev/engine/ddp.py --help` 自检；然后告诉用户：

   - mock 模式已就绪，可直接体验：`/submit` 建单 → `/sds-design <工单号>` → `/start <工单号>`；
   - live 模式：`tracker` / `quality` 用 CLI（JSON stdout）；
     `knowledge` 三选一：`ov-cli`（原生 ov CLI）/ `http`（配置
     `knowledge.ov.url` + `OV_API_KEY`，无需本地 CLI）/ `custom-cli`（自定义包装）；
     `platform` 可插拔——`cli`（JSON stdout）/ `doris|starrocks|mysql|tidb…`
     （pymysql 直连，需 `py -3 -m pip install pymysql`）/ `paimon|flink|spark|trino…`
     （配置 `platform.command.run` 命令模板）/ `mcp`（预留：会话内由模型调用
     `mcp__<server>__<tool>`，引擎不直调）。详见 docs/REPRODUCTION-NOTES.md 1.2。
   - `.data-dev/engine` 由插件维护：工具改写会被 engine_readonly 拦截；若提示
     engine_integrity 不一致，重新执行本命令同步引擎即可。
6b. **hook 启动器体检（不能跳过，非 Windows 必做）**：

   - 先跑 `py -3 <插件根>/tools/adapt_hooks.py`（Windows）或
     `python3 <插件根>/tools/adapt_hooks.py`（macOS/Linux），把 hooks 文件里的
     启动器切成本机可用的那个；
   - 再跑同一个命令加 `--check`。**返回非零就说明 hooks 文件里的启动器不在本机
     PATH 上**——宿主会把 hook 失败当成放行，插件看着装好了，十几道硬门一道都不生效。
     把 `--check` 的实际输出告诉用户，不要只说"已就绪"。
   - 这一步对 Windows 也要跑一次（确认 `py -3` 真的在 PATH 上）。

> 注意：`/setup` 必须在每个数据研发项目目录执行一次；hooks（硬门）由插件自动加载，
> 无需在项目里复制 hooks。
