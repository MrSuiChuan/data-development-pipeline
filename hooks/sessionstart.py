#!/usr/bin/env python3
"""SessionStart hook: setup hint + active-ticket status + pending-confirmation warn."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from common import (  # noqa: E402
    ENGINE_DIR,
    command_hint,
    emit,
    host_name,
    load_payload,
    plugin_version,
    project_root,
)

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from core import context  # noqa: E402
from core.state import Pipeline  # noqa: E402


def main() -> None:
    payload = load_payload()
    root = project_root(payload)
    version = plugin_version()
    host = host_name()
    setup_cmd = command_hint("setup")
    submit_cmd = command_hint("submit")
    sds_cmd = command_hint("sds-design")
    start_cmd = command_hint("start")
    if root is None:
        emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": (
                        f"data-development-plugin v{version} 已加载（{host}）。当前项目未初始化："
                        f"运行 {setup_cmd} 建立 .data-dev 运行时；数据需求从 {submit_cmd} 建单开始。"
                    )
                }
            }
        )
        return
    try:
        config = context.load_config(root)
        ticket = context.current_ticket(root)
        lines = [f"data-development-plugin v{version}（{host}，mode={config.get('mode', 'mock')}）"]
        if ticket:
            pipeline = Pipeline(context.ticket_dir(root, ticket))
            if pipeline.exists():
                summary = pipeline.summary()
                lines.append(
                    f"当前工单 {ticket}: P{summary['phase']} {summary['phase_name']} "
                    f"level={summary['level']} run_mode={summary['run_mode']} sds={summary['sds_status']}"
                )
                if summary["awaiting_confirmation"]:
                    confirm_hint = "用户确认" if host == "Codex" else "AskUserQuestion"
                    lines.append(
                        f"⚠️ awaiting_confirmation=true：交付物写入与生产 CLI 被锁，先完成{confirm_hint}确认。"
                    )
                if summary.get("sds_status") == "published" and summary["phase"] < 2:
                    lines.append(f"SDS 已发布：运行 {start_cmd} <工单> 进入研发态。")
        else:
            lines.append(
                f"无当前工单：{submit_cmd} 建单，或 {sds_cmd} <工单号> / {start_cmd} <工单号> 指定。"
            )
        emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}})
    except Exception as exc:  # session start should never fail the session
        emit({})


if __name__ == "__main__":
    main()
