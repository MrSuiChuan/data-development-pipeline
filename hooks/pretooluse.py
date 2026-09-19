#!/usr/bin/env python3
"""PreToolUse hook: hard gates (decision:block) for -style tool calls."""

from __future__ import annotations

import os
import sys

from common import ENGINE_DIR, emit, fail_open, load_payload, project_root, trace_payload  # noqa: E402

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

# 注意：core 引擎是惰性导入的。这台机器上「python 启动 + 导入整个引擎」要 20 秒左右，
# 而宿主 hook 有超时；超时会被当成 fail-open 放行。因此不相关的工具走快速通道，
# 只有真正需要门控的工具才付这份导入成本。
_GATED_TOOLS = {
    "bash",
    "task",
    "executecommand",
    "cli",
    "exec_command",
    "local_shell",
    "shell_command",
    "shell",
    "write",
    "edit",
    "multiedit",
    "notebookedit",
    "apply_patch",
    "applypatch",
    "patch",
    "edit_file",
    "write_file",
}
_GATED_SUBSTRINGS = ("tracker", "platform", "quality", "knowledge", "viking")


def _needs_gate(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return lowered in _GATED_TOOLS or any(part in lowered for part in _GATED_SUBSTRINGS)


def block_output(decision) -> dict:
    """Codex/Claude Code 的 PreToolUse 输出 schema 是 additionalProperties:false。

    只允许这几个键（多一个就会被宿主判为非法输出并忽略整个决策——实测 Codex 会
    静默放行），所以这里严格按 schema 输出，并用 exit 0 + JSON 决策表达阻断。
    """
    message = f"[data-development-plugin:{decision.gate}] 工具调用被硬门拦截。{decision.reason}"
    return {
        "decision": "block",
        "reason": message,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        },
    }


def main() -> None:
    payload = load_payload()
    trace_payload("PreToolUse", payload)
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    if not _needs_gate(tool_name):
        emit({})
        return
    root = project_root(payload)
    from core import gates  # 惰性导入：只为需要门控的工具付启动成本

    try:
        decision = gates.decide_pre_tool(payload, root)
    except Exception as exc:  # never lock the session because of our own bug
        fail_open(f"{type(exc).__name__}: {exc}")
        return
    if decision.blocked():
        emit(block_output(decision), exit_code=0)
    emit({})


if __name__ == "__main__":
    main()
