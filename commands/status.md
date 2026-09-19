---
description: 查看当前/指定工单的流水线状态（phase、level、门控 context 键、交付物清单）
argument-hint: [<T-xxxx>]
allowed-tools: [Bash, Read]
---

# /status [<T-xxxx>]

运行：

```
py -3 .data-dev/engine/ddp.py status --ticket <T-xxxx>
py -3 .data-dev/engine/ddp.py status --gate --ticket <T-xxxx>   # 直接诊断当前阶段出口门
```

无工单号时使用 `.data-dev/current.json`。汇报：当前 Phase、难度、run_mode、
SDS 状态、completed 列表、awaiting_confirmation、context 证据键（verify_result /
self_check_result / code_diff_done 等）以及交付物文件清单。若某门未过，直接给出下一步命令。
