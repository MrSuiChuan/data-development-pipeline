#!/usr/bin/env python3
"""PostToolUse hook: validator soft-feedback + context-key bridge.

PostToolUse cannot block (the tool already ran). Its job is the second half of
the enforcement loop: re-validate the artifact that was just written, write the
machine-readable result into ``_state.json`` context keys, and inject a short
``additionalContext`` so the model repairs before the next hard gate runs.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from common import ENGINE_DIR, emit, fail_open, load_payload, project_root, trace_payload  # noqa: E402

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from core import context, gates, validators  # noqa: E402
from core.state import Pipeline  # noqa: E402


def _tool_input(payload: dict) -> dict:
    value = payload.get("tool_input") or {}
    return value if isinstance(value, dict) else {"command": str(value)}


def _changed_file(payload: dict, tool_name: str) -> tuple:
    ti = _tool_input(payload)
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        file_path = str(
            ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
            or ti.get("file") or ""
        )
        content = str(ti.get("content") or ti.get("new_string") or ti.get("new_content") or "")
        return file_path, content
    return "", ""


def _ticket_dir_for(root: Path, file_path: str) -> tuple:
    """Return (ticket_dir, relative_path) if file_path lives in a ticket workspace."""
    if not file_path:
        return None, None
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
    parts = [p.lower() for p in path.parts]
    for idx, part in enumerate(parts):
        if part == ".data-dev" and idx + 2 < len(parts) and parts[idx + 1] == "tickets":
            ticket_dir = Path(*path.parts[: idx + 3])
            rel = path.relative_to(ticket_dir)
            return ticket_dir, rel
    return None, None


def _sync_validator_keys(root: Path, ticket_dir: Path, rel: Path) -> list:
    """Re-validate the touched artifact and bridge results into context keys."""
    notes: list = []
    pipeline = Pipeline(ticket_dir)
    if not pipeline.exists():
        return notes
    rel_lower = str(rel).lower()
    changed = []
    if rel_lower.startswith(("ddl/", "etl/", "dp_task/")):
        issues = validators.scan_sql_tree(ticket_dir)
        blockers = validators.blockers(issues)
        pipeline.set_context("sql_compliance_result", "failed" if blockers else "passed", log=False)
        changed += blockers[:3]
    if rel_lower == "etl_dev_exec.json":
        ev = context.load_json(ticket_dir / "etl_dev_exec.json") or {}
        ok_ev, _reason = validators.execution_evidence_ok(ev, context.load_config(root).get("limits") or {})
        pipeline.set_context("etl_dev_evidence_ok", ok_ev, log=False)
    if rel_lower == "code_diff.html":
        pipeline.set_context("code_diff_done", True, log=False)
    if rel_lower == "test_runs.json":
        state = pipeline.state
        issues = validators.validate_phase4_testing(ticket_dir, state)
        pipeline.set_context("test_evidence_ok", not validators.has_blockers(issues), log=False)
        changed += validators.blockers(issues)[:3]
    if rel_lower in ("delivery_summary.md", "deploy_log.json"):
        log_data = context.load_json(ticket_dir / "deploy_log.json")
        pipeline.set_context("deploy_executed", isinstance(log_data, dict), log=False)
    if rel_lower == "deploy_plan.json":
        plan = context.load_json(ticket_dir / "deploy_plan.json") or {}
        issues = validators.validate_deploy_plan_dict(plan, pipeline.ticket)
        pipeline.set_context("deploy_plan_valid", not validators.has_blockers(issues), log=False)
        changed += validators.blockers(issues)[:3]
    pipeline.save()
    for issue in changed:
        notes.append(str(issue))
    return notes


def _mark_reads(payload: dict, root: Path, tool_name: str) -> None:
    ti = _tool_input(payload)
    command = str(ti.get("command") or ti.get("cmd") or "")
    if tool_name == "Bash" and re.search(r"(read_task|read-task|readTask|show|query-workitem)", command):
        m = re.search(r"--(?:name|task|id)\s+([\w.-]+)", command)
        if m:
            gates.mark_read(root, m.group(1))


def main() -> None:
    payload = load_payload()
    trace_payload("PostToolUse", payload)
    tool_name = str(payload.get("tool_name") or "")
    root = project_root(payload)
    if root is None:
        emit({})
        return
    notes: list = []
    file_path, _content = _changed_file(payload, tool_name)
    ticket_dir, rel = _ticket_dir_for(root, file_path)
    try:
        if ticket_dir:
            notes = _sync_validator_keys(root, ticket_dir, rel)
        _mark_reads(payload, root, tool_name)
    except Exception as exc:
        fail_open(f"{type(exc).__name__}: {exc}")
        return
    if not notes:
        emit({})
    text = "[data-development-plugin 校验反馈（软提示，不阻断）]\n" + "\n".join(f"- {n}" for n in notes)
    emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}})


if __name__ == "__main__":
    main()
