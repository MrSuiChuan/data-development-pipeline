---
description: 显式阶段转场（引擎执行 stage_gate；回跳需 --allow-back --reason）
argument-hint: --phase <N> --ticket <T-xxxx> --reason <依据> [--allow-back]
allowed-tools: [Bash, Read]
---

# /advance — 状态机转场

通常由各阶段命令自动调用，也可手动显式转场。

```
py -3 .data-dev/engine/ddp.py advance --phase <N> --ticket <T-xxxx> --reason "<依据>"
py -3 .data-dev/engine/ddp.py advance --phase 3 --ticket T-0001 --allow-back --reason "copilot 穿插改口径需回 P3"
```

规则：

- **前向**只允许逐级（P2→P3→P4→P5→P6→完成）；`advance` 会先跑当前阶段出口 stage_gate，
  有 FAIL/HALTED 缺陷就拒绝推进并列出缺陷；
- **回跳**必须 `--allow-back --reason`：跳过前向门控、不追加 completed（copilot 穿插用）；
- 每次转场写 `transition_log`，状态只认机器证据，不认 AI 口头声明。

被拒绝时：读取返回的 issues，修完交付物后再试；不要用 `--allow-back` 绕过前向门控。
