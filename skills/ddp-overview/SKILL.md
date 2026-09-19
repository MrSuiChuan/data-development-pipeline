---
name: ddp-overview
description: 查看 data-development-plugin 全貌：三层架构、六阶段、命令/Agent/Skill/Workflow 清单（Codex 入口，等价于 /overview）
---

# ddp-overview（Codex 版 `/overview`）

> 本文件由 `tools/build_codex_surface.py` 从 `commands/overview.md` 生成，请勿手改。
> 改流程请改命令源文件后重跑生成器。
> 命令里的 `py -3` 是 Windows 写法；macOS/Linux 换成 `python3`。

# data-development-plugin 总览

把离线数据任务研发工艺锁死成“六阶段流水线”的 Claude Code 插件。三层嵌套：

1. **命令编排**（commands/*.md）——给 AI 看的流程剧本；
2. **状态机**（`_state.json` + `engine/core/state.py`）——机器可判的“做到哪了”；
3. **Hook 硬门**（hooks/*）——PreToolUse `decision:block` 真阻断；PostToolUse 验证器软提示
   把结果写成 `_state.json` context 键，被硬门消费，形成闭环。

## 六阶段

| Phase | 名称 | 交付物 | 出口门 |
| --- | --- | --- | --- |
| P1 | SDS | sds_design.json + verification/ + 终版贴单 | 八条硬规则 + 确认门 |
| P2 | 架构 | architecture_doc.md + pipeline.html | 难度级别 + 链路图 |
| P3 | 研发 | ddl/ etl/ dp_task/ + etl_dev_exec.json | 17 项 SQL 检查 + 真跑证据 + code_diff |
| P4 | 测试 | test_report.md + test_runs.json | ✅ TC ↔ SUCCESS+query_id 机器比对 |
| P5 | 部署 | delivery_summary.md + deploy_log.json | 三道不可逆确认门 + [已确认发布] |
| P6 | 语义层 | semantic_update_summary.md | 用户拍板 + 模型信息 |

## 命令

data-development-plugin:ddp-setup data-development-plugin:ddp-submit data-development-plugin:ddp-sds-design data-development-plugin:ddp-start data-development-plugin:ddp-copilot data-development-plugin:ddp-resume data-development-plugin:ddp-advance data-development-plugin:ddp-status

## 常用引擎命令（Windows 用 py -3，POSIX 用 python3）

`py -3 .data-dev/engine/ddp.py --help` 查看全部；`demo` 可在 mock 目录跑通全流程。
