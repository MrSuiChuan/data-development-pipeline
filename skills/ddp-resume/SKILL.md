---
name: ddp-resume
description: 跨会话续接：按 _state.json 恢复设计态或研发态（Codex 入口，等价于 /resume）
---

# ddp-resume（Codex 版 `/resume`）

> 本文件由 `tools/build_codex_surface.py` 从 `commands/resume.md` 生成，请勿手改。
> 改流程请改命令源文件后重跑生成器。
> 命令里的 `py -3` 是 Windows 写法；macOS/Linux 换成 `python3`。

# data-development-plugin:ddp-resume [<T-xxxx>] — 恢复上次进度

Claude Code 新会话不记得上下文，但 `.data-dev/tickets/<T-xxxx>/_state.json` 记得。

## 步骤

1. `py -3 .data-dev/engine/ddp.py status --ticket <T-xxxx>`（未给工单号则用当前工单）。
2. 按 `sds_design.status` 三态分流：
   - `published` 且 phase<=1 → 走 `data-development-plugin:ddp-start`（准入门会重建上下文）；
   - phase>=2 → 加载 `_context.md` 摘要与 `requirement_spec.md`，从当前 Phase 继续
     （P3+ 重跑该阶段的 compliance / evidence 校验后继续）；
   - `in_progress/review_pending/review_backfill/pending_confirm/blocked` →
     回到 `data-development-plugin:ddp-sds-design` 的设计态流程继续；
   - `finished` → 汇报闭环结果，不再开工。
3. 恢复时先重新加载方法论 skill（`sds-design` / `architecture-design` / `data-develop` /
   `data-testing` / `data-deploy` / `data-semantic`，按当前 phase 只加载对应一个）。
4. 若 `awaiting_confirmation=true`：提示用户上次确认未完成，先处理该确认再继续。
