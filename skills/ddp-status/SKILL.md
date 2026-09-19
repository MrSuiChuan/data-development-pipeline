---
name: ddp-status
description: 查看当前/指定工单的流水线状态（phase、level、门控 context 键、交付物清单）（Codex 入口，等价于 /status）
---

# ddp-status（Codex 版 `/status`）

> 本文件由 `tools/build_codex_surface.py` 从 `commands/status.md` 生成，请勿手改。
> 改流程请改命令源文件后重跑生成器。
> 命令里的 `py -3` 是 Windows 写法；macOS/Linux 换成 `python3`。

# data-development-plugin:ddp-status [<T-xxxx>]

运行：

```
py -3 .data-dev/engine/ddp.py status --ticket <T-xxxx>
py -3 .data-dev/engine/ddp.py status --gate --ticket <T-xxxx>   # 直接诊断当前阶段出口门
```

无工单号时使用 `.data-dev/current.json`。汇报：当前 Phase、难度、run_mode、
SDS 状态、completed 列表、awaiting_confirmation、context 证据键（verify_result /
self_check_result / code_diff_done 等）以及交付物文件清单。若某门未过，直接给出下一步命令。
