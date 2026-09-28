#!/usr/bin/env python3
"""data-development-plugin engine CLI.

The slash commands (/submit /sds-design /start /advance /copilot /resume)
drive Claude Code through this engine. Hooks independently enforce the same
gates on every Bash/Write tool call, so the engine re-checks before acting
(defense in depth even when a session runs with hooks disabled).

Usage::

    python engine/ddp.py <verb> [--ticket T-0001] [--root <project>] ...

Run ``python engine/ddp.py --help`` for the verb list and ``demo`` for an
end-to-end happy-path walkthrough in mock mode.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import clients, context, engines, gates, spec, state, validators  # noqa: E402
from core.state import Pipeline  # noqa: E402

# Windows 上 stdout/stderr 被重定向（管道、写文件、CI）时用 cp1252/cp936，
# HELP 与各种中文提示会直接 UnicodeEncodeError（实测 `--help` 就崩）。
# 交互式控制台走 PEP 528 的宽字符 API，本来就支持中文，这步是空操作。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT: Optional[Path] = None
CONFIG: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# tiny CLI helpers
# ---------------------------------------------------------------------------
def parse_args(argv: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def put(key: str, value: str) -> None:
        # 同时保留原始键与下划线键：--description-file 与 --save-evidence 两种写法都能被读到
        out[key] = value
        out[key.replace("-", "_")] = value

    for i, token in enumerate(argv):
        if token.startswith("--"):
            key = token[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                put(k, v)
            else:
                value = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else ""
                put(key, value if value else "1")
        elif token.startswith("-") and len(token) > 1 and not token.startswith("--"):
            value = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "1"
            put(token[1:], value)
    return out


def engine_selfcheck(root: Path) -> List[str]:
    """Verify the project-local engine copy against .engine-manifest.json."""
    engine_dir = root / context.APP_DIR_NAME / "engine"
    manifest_path = engine_dir / ".engine-manifest.json"
    if not manifest_path.is_file():
        return []
    manifest = context.load_json(manifest_path, {}) or {}
    files = manifest.get("files") or {}
    mismatched: List[str] = []
    for rel, expected in sorted(files.items()):
        target = engine_dir / rel
        actual = context.sha256_file(target) if target.is_file() else "MISSING"
        if actual != expected:
            mismatched.append(rel)
    return mismatched


def boot(argv: List[str]) -> Dict[str, str]:
    global ROOT, CONFIG
    flags = parse_args(argv)
    root_arg = flags.get("root")
    if root_arg:
        ROOT = Path(root_arg).resolve()
    elif context.project_root():
        ROOT = context.project_root()
    else:
        ROOT = Path.cwd()
    repair_requested = any(token == "engine" for token in argv) and any(token in ("sync", "--sync") for token in argv)
    mismatched = engine_selfcheck(ROOT) if not repair_requested else []
    if mismatched:
        print(
            json.dumps(
                {
                    "ok": False,
                    "blocked": True,
                    "gate": "engine_integrity",
                    "reason": ".data-dev/engine 与插件根引擎不一致（被改写？），拒绝执行。请重跑 /setup 同步引擎。",
                    "mismatched": mismatched[:10],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(2)
    if (ROOT / context.APP_DIR_NAME / context.CONFIG_FILE).is_file():
        CONFIG = context.load_config(ROOT)
    else:
        CONFIG = context.default_config()
    return flags


def ticket(flags: Dict[str, str]) -> str:
    tid = context.resolve_ticket(ROOT, flags.get("ticket") or flags.get("id"))
    if not tid:
        print("无法确定工单：请带 --ticket <id>", file=sys.stderr)
        sys.exit(2)
    return tid


def req_dir(flags: Dict[str, str]) -> Path:
    return context.ticket_dir(ROOT, ticket(flags))


def tracker() -> clients.TrackerClient:
    return clients.make_tracker(CONFIG, ROOT)


def platform() -> clients.PlatformClient:
    return clients.make_platform(CONFIG, ROOT)


def emit(obj: Any, json_mode: bool = True) -> None:
    if not json_mode and isinstance(obj, str):
        payload = obj
    else:
        payload = json.dumps(obj, ensure_ascii=False, indent=2)
    try:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        print(payload)


def enrich_state(state: Dict[str, Any]) -> None:
    """Inject config-derived option sections before running a stage gate."""
    state.setdefault("mode", CONFIG.get("mode") or "mock")
    state.setdefault("limits", CONFIG.get("limits") or {})
    state.setdefault("modeling", CONFIG.get("modeling") or {})
    state.setdefault("semantic", CONFIG.get("semantic") or {})
    state.setdefault("deploy", CONFIG.get("deploy") or {})


def config_security_warns(config: Dict[str, Any]) -> List[str]:
    warns: List[str] = []
    platform = config.get("platform") or {}
    if str(platform.get("password") or "").strip():
        warns.append(
            "platform.password 明文写在 config.json：建议删除并改用 password_env "
            "（如 DDP_PLATFORM_PASSWORD），避免密钥随项目被提交。"
        )
    return warns


def record_execution(sql_text: str, result: Any) -> None:
    """Persist every engine-run execution receipt into .runtime/executions.json."""
    if not isinstance(result, dict):
        return
    query_id = str(result.get("query_id") or "").strip()
    if not query_id:
        return
    ledger_path = context.ledger_dir(ROOT) / "executions.json"
    ledger = context.load_json(ledger_path, {"runs": []}) or {"runs": []}
    ledger.setdefault("runs", []).append(
        {
            "query_id": query_id,
            "engine": str(result.get("engine") or ""),
            "env": str(result.get("env") or ""),
            "project": str(result.get("project") or ""),
            "status": str(result.get("status") or "SUCCESS"),
            "executed_at": str(result.get("executed_at") or result.get("run_at") or context.utcnow()),
            "duration_s": result.get("duration_s"),
            "affected_rows": result.get("affected_rows"),
            "sql_hash": context.sha256_text(sql_text or ""),
        }
    )
    context.write_json(ledger_path, ledger)


def check_gate(command_text: str) -> None:
    """Re-run the PreToolUse gate inside the engine before acting."""
    decision = gates.decide_cli_command(command_text, ROOT)
    if decision.blocked():
        emit(
            {
                "ok": False,
                "blocked": True,
                "gate": decision.gate,
                "reason": decision.reason,
            }
        )
        sys.exit(3)


# ---------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------
def verb_setup(flags: Dict[str, str]) -> None:
    mode = flags.get("mode", "mock")
    root = context.ensure_app_dir(ROOT, mode=mode)
    print(json.dumps({"ok": True, "root": str(root), "mode": mode}, ensure_ascii=False))


def verb_engine(flags: Dict[str, str]) -> None:
    sub = flags.get("sub") or flags.get("1", "")
    if sub != "sync":
        print("engine 子命令: sync --from <插件引擎根>", file=sys.stderr)
        sys.exit(2)
    source = Path(flags.get("from", "")).resolve()
    if not (source / "ddp.py").is_file() or not (source / "core").is_dir():
        print(f"来源不是引擎目录: {source}（需要 ddp.py + core/）", file=sys.stderr)
        sys.exit(2)
    dst = ROOT / context.APP_DIR_NAME / "engine"
    context.copy_tree(source, dst)
    mismatched = engine_selfcheck(ROOT)
    if mismatched:
        emit(
            {
                "ok": False,
                "blocked": True,
                "gate": "engine_integrity",
                "reason": "同步后完整性自检失败，请检查来源引擎",
                "mismatched": mismatched[:10],
            }
        )
        sys.exit(1)
    emit({"ok": True, "engine_root": str(dst), "source": str(source)})


def verb_ticket(flags: Dict[str, str]) -> None:
    sub = flags.get("sub") or flags.get("action") or flags.get("1") or ""
    if sub == "create":
        title = flags.get("title", "")
        description = flags.get("description", "")
        if flags.get("description_file"):
            description = context.read_text(Path(flags["description_file"]))
        if not title or not description:
            print("ticket create 需要 --title 与 --description/--description-file", file=sys.stderr)
            sys.exit(2)
        issues = validators.validate_submit_quality(title, description)
        blockers_ = validators.blockers(issues)
        if blockers_:
            emit({"ok": False, "blocked": True, "gate": "submit_quality_gate", "issues": validators.issues_to_dicts(blockers_)})
            sys.exit(1)
        item = tracker().create_work_item(title, description, flags.get("template", "数据任务"))
        req = context.ticket_dir(ROOT, item["id"])
        Pipeline.init(req, item["id"], title, description, context.sha256_text(description))
        context.set_current_ticket(ROOT, item["id"])
        emit({"ok": True, "workitem": item, "quality": [i.to_dict() for i in issues]})
        return
    tid = ticket(flags)
    if sub == "show":
        emit(tracker().query(tid))
    elif sub == "comments":
        emit(tracker().comments(tid))
    elif sub == "verify-latest":
        result = spec.verify_latest_comment(tracker().comments(tid))
        emit({"ok": bool(result and result["verified"]), "result": result})
        if not (result and result["verified"]):
            sys.exit(2)
    else:
        print(__verbs_help())


def _first_positional() -> str:
    for token in sys.argv[1:]:
        if not token.startswith("-"):
            if token in ("create", "show", "comments", "verify-latest", "add", "add-comment"):
                return token
    return ""


def verb_comment(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    content = flags.get("content", "")
    if flags.get("file"):
        content = context.read_text(Path(flags["file"]))
    anchor = flags.get("anchor", "")
    if not content:
        print("comment add 需要 --content 或 --file", file=sys.stderr)
        sys.exit(2)
    emit(tracker().add_comment(tid, content, anchor))


def verb_sds(flags: Dict[str, str]) -> None:
    sub = flags.get("sub", "")
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    if not pipeline.exists():
        print(f"工单 {tid} 没有本地状态（先 /submit）", file=sys.stderr)
        sys.exit(2)
    if sub == "enter":
        # P1 的唯一入口：state.enter_sds 早已存在，但之前没有 CLI 暴露，
        # 宿主里只能绕过状态机直接改 _state.json（实测 Codex 就卡在这里）。
        mode = flags.get("mode") or "verify"
        if mode not in ("verify", "generate"):
            print("--mode 必须是 verify|generate", file=sys.stderr)
            sys.exit(2)
        pipeline.enter_sds(mode)
        pipeline.save()
        emit(
            {
                "ok": True,
                "mode": mode,
                "sds_status": pipeline.sds.get("status"),
                "phase": pipeline.state.get("current_phase"),
            }
        )
        return
    if sub == "validate":
        issues = validators.validate_sds_design(
            req,
            limits=CONFIG.get("limits") or {},
            require_confirmed=False,
            kb_prefix=clients.kb_prefix(CONFIG),
            modeling=CONFIG.get("modeling") or {},
        )
        if flags.get("confirm"):
            marker = context.read_text(req / "_user_confirmation.md")
            if "[已确认]" not in marker:
                emit(
                    {
                        "ok": False,
                        "blocked": True,
                        "gate": "sds_confirm_gate",
                        "reason": "validate --confirm 需要 _user_confirmation.md 含 [已确认]（AskUserQuestion 记录）",
                    }
                )
                sys.exit(1)
            sds_obj = context.load_json(req / "sds_design.json") or {}
            sds_obj["confirmed"] = True
            context.write_json(req / "sds_design.json", sds_obj)
            pipeline.sds["confirmed"] = True
            pipeline.save()
            issues = [i for i in issues if i.code != "sds.confirm.pending"]
        elif flags.get("final"):
            issues = [i for i in issues if i.code != "sds.confirm.pending"]
        emit({"ok": not validators.has_blockers(issues), "issues": validators.issues_to_dicts(issues)})
        if validators.has_blockers(issues):
            sys.exit(1)
    elif sub == "render":
        spec_text = spec.render_sds_spec(req)
        context.write_text(req / "sds_design_spec.md", spec_text)
        emit({"ok": True, "file": "sds_design_spec.md"})
    elif sub == "build-comment":
        payload = spec.build_ticket_comment(req)
        context.write_text(req / "_sds_comment.md", payload["content_md"])
        emit({"ok": True, **payload})
    elif sub == "bridge":
        workitem = tracker().query(tid)
        result = spec.bridge_to_spec(req, workitem)
        pipeline.state["level"] = result["level"]
        pipeline.sds["ticket_hash"] = result["ticket_hash"]
        pipeline.state["ticket_hash"] = result["ticket_hash"]
        pipeline.log_transition("bridge", f"requirement_spec.md 合成完成 level={result['level']}")
        pipeline.save()
        emit({"ok": True, **result})
    elif sub == "publish":
        version = int(flags.get("version", "1") or 1)
        pipeline.mark_published(version)
        emit({"ok": True, "sds_status": state.SDS_PUBLISHED, "version": version})
    elif sub == "backfill-resolve":
        action = flags.get("action", "")
        if action not in ("synced", "declined"):
            print("--action 必须是 synced|declined", file=sys.stderr)
            sys.exit(2)
        pipeline.resolve_backfill(action, flags.get("reason", ""))
        emit({"ok": True, "backfill_resolved": action})
    elif sub == "review":
        action = flags.get("action", "")  # refresh | supply
        pipeline.bump_kb_round()
        rounds = pipeline.sds.get("kb_round", 0)
        if rounds > int(CONFIG.get("limits", {}).get("kb_round_max", 2)):
            pipeline.set_sds_status(state.SDS_BLOCKED, f"kb_round {rounds} 超限，复核门终止")
            emit({"ok": False, "blocked": True, "reason": "kb_round 超限，复核门 blocked 终止"})
            sys.exit(1)
        pipeline.set_sds_status(state.SDS_REVIEW_PENDING, f"人工复核({action})，kb_round={rounds}")
        emit({"ok": True, "kb_round": rounds, "status": state.SDS_REVIEW_PENDING})
    elif sub == "status":
        pipeline.sds["status"] = state.SDS_IN_PROGRESS
        pipeline.save()
        emit(pipeline.summary())
    else:
        print("sds 子命令: validate|render|build-comment|bridge|backfill-resolve|review|status")


def verb_start(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    steps = []
    # 1. 工单校验
    try:
        workitem = tracker().query(tid)
        steps.append({"step": 1, "ok": True, "note": "工单存在"})
    except clients.ClientError as exc:
        emit({"ok": False, "blocked": True, "gate": "start_admission", "step": 1, "reason": str(exc)})
        sys.exit(1)
    # 2. 终版锚点
    latest = spec.verify_latest_comment(tracker().comments(tid))
    if not (latest and latest["verified"]):
        emit(
            {
                "ok": False,
                "blocked": True,
                "gate": "start_admission",
                "step": 2,
                "reason": "工单无【数据概要设计 vN】终版评论（锚头+json块+确认行），请先 /sds-design。",
            }
        )
        sys.exit(1)
    steps.append({"step": 2, "ok": True, "note": "SDS 终版锚点存在"})
    # 3. 内容重验：从评论 json 块落盘并重跑八条规则（不信任本地文件）
    text = latest["comment"].get("content_md", "")
    block = context.extract_json_block(text)
    if block is None:
        emit({"ok": False, "blocked": True, "step": 3, "reason": "评论中找不到 json 机读块，双源降级失败。"})
        sys.exit(1)
    context.write_json(req / "sds_design.json", block)
    issues = validators.validate_sds_design(
        req,
        limits=CONFIG.get("limits") or {},
        kb_prefix=clients.kb_prefix(CONFIG),
        modeling=CONFIG.get("modeling") or {},
    )
    if validators.has_blockers(issues):
        emit(
            {
                "ok": False,
                "blocked": True,
                "gate": "start_admission",
                "step": 3,
                "reason": "终版评论重跑八条硬规则失败（不信任贴单内容）",
                "issues": validators.issues_to_dicts(validators.blockers(issues)),
            }
        )
        sys.exit(1)
    steps.append({"step": 3, "ok": True, "note": "贴单内容重验通过"})
    # 4. 口径变更检测
    current_hash = context.sha256_text(str(workitem.get("description_md", "")))
    recorded = pipeline.sds.get("ticket_hash") or pipeline.state.get("ticket_hash") or ""
    if current_hash and recorded and current_hash != recorded:
        emit(
            {
                "ok": False,
                "blocked": True,
                "gate": "start_admission",
                "step": 4,
                "reason": "工单正文 hash 与 SDS 落库 ticket_hash 不一致：口径已变更。必须 AskUserQuestion 提示用户重跑 /sds-design（不自动作废）。",
            }
        )
        sys.exit(1)
    steps.append({"step": 4, "ok": True, "note": "口径 hash 一致"})
    # 5. 上下文重建
    if not (req / "requirement_spec.md").is_file():
        spec.bridge_to_spec(req, workitem)
    level = pipeline.level or pipeline.sds.get("level_suggestion")
    if not level:
        emit({"ok": False, "step": 5, "reason": "level 未落盘。"})
        sys.exit(1)
    pipeline.set_level(level, "start 准入上下文重建")
    run_mode = "autopilot" if flags.get("autopilot") else "interactive"
    pipeline.enter_dev_from_sds(run_mode)
    tracker().set_status(tid, "开发中", reason="start 准入门通过（唯一进入研发态的流转点）")
    context.set_current_ticket(ROOT, tid)
    steps.append({"step": 5, "ok": True, "note": f"上下文重建完成，进入 P2（{run_mode}）"})
    emit({"ok": True, "steps": steps, "summary": pipeline.summary()})


def verb_advance(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    target = int(flags.get("phase", "0") or 0)
    reason = flags.get("reason", "")
    allow_back = bool(flags.get("allow-back"))

    def gate(current_phase: int) -> List[Any]:
        enriched = dict(pipeline.state)
        enrich_state(enriched)
        return validators.stage_gate(current_phase, req, enriched, clients.kb_prefix(CONFIG))

    result = pipeline.advance(target, reason, allow_back=allow_back, gate=gate)
    emit(result)
    if not result.get("ok"):
        sys.exit(1)


def verb_status(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    data = pipeline.summary()
    data["files"] = sorted(p.name for p in req.iterdir() if p.is_file())
    data["config_warns"] = config_security_warns(context.load_config(ROOT))
    if flags.get("gate") or flags.get("1") == "gate":
        enriched = dict(pipeline.state)
        enrich_state(enriched)
        issues = validators.stage_gate(pipeline.phase, req, enriched, clients.kb_prefix(CONFIG))
        data["gate"] = {
            "phase": pipeline.phase,
            "blockers": validators.issues_to_dicts(validators.blockers(issues)),
            "warns": validators.issues_to_dicts([i for i in issues if i.severity == "WARN"]),
        }
    emit(data)


def verb_report(flags: Dict[str, str]) -> None:
    """Governance report: timeline + evidence + decisions for a ticket."""
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    sds = context.load_json(req / "sds_design.json") or {}
    etl_ev = context.load_json(req / "etl_dev_exec.json") or {}
    ok_etl, etl_reason = validators.execution_evidence_ok(etl_ev, CONFIG.get("limits") or {})
    test_runs = context.load_json(req / "test_runs.json") or {}
    run_list = test_runs.get("runs") if isinstance(test_runs.get("runs"), list) else []
    deploy_log = context.load_json(req / "deploy_log.json") or {}
    rollback_log = context.load_json(req / "rollback_log.json") or {}
    decisions = pipeline.state.get("autopilot_decisions") or []
    report = {
        "ticket": pipeline.ticket,
        "title": pipeline.state.get("title") or pipeline.ticket,
        "phase": pipeline.phase,
        "phase_name": pipeline.phase_name(),
        "finished": pipeline.state.get("finished", False),
        "run_mode": pipeline.state.get("run_mode"),
        "level": pipeline.level,
        "sds_status": pipeline.sds.get("status"),
        "completed": pipeline.state.get("completed", []),
        "timeline": pipeline.state.get("transition_log", []),
        "evidence": {
            "sds_design": {"exists": bool(sds), "confirmed": bool(sds.get("confirmed"))},
            "etl_dev_exec": {"ok": ok_etl, "reason": etl_reason or None},
            "test_runs": {
                "exists": bool(test_runs),
                "total": len(run_list),
                "failed": sum(1 for r in run_list if isinstance(r, dict) and r.get("status") == "FAILED"),
            },
            "deploy_log": {"exists": bool(deploy_log), "env": deploy_log.get("env") or ""},
            "rollback_log": {"exists": bool(rollback_log)},
        },
        "autopilot_decisions": {
            "total": len(decisions),
            "risk": [d for d in decisions if d.get("risk")],
        },
        "config_warns": config_security_warns(CONFIG),
        "files": sorted(p.name for p in req.iterdir() if p.is_file()),
    }
    if flags.get("write") or flags.get("md"):
        lines = [
            "# governance_report.md",
            "",
            f"- ticket: {report['ticket']} · {report['title']}",
            f"- phase: P{report['phase']} {report['phase_name']} · finished={report['finished']}",
            f"- run_mode: {report['run_mode']} · level: {report['level']} · sds: {report['sds_status']}",
            f"- completed: {','.join(map(str, report['completed'])) or '-'}",
            "",
            "## 证据",
            "",
            f"- SDS: exists={report['evidence']['sds_design']['exists']}, confirmed={report['evidence']['sds_design']['confirmed']}",
            f"- ETL 执行: {report['evidence']['etl_dev_exec']['ok']} {report['evidence']['etl_dev_exec']['reason'] or ''}",
            f"- 测试: total={report['evidence']['test_runs']['total']}, failed={report['evidence']['test_runs']['failed']}",
            f"- 部署日志: exists={report['evidence']['deploy_log']['exists']}, env={report['evidence']['deploy_log']['env'] or '-'}",
            f"- 回滚日志: exists={report['evidence']['rollback_log']['exists']}",
            "",
            "## 时间线",
            "",
        ]
        for entry in report["timeline"]:
            lines.append(f"- [{entry.get('at')}] {entry.get('kind')}: {entry.get('message')}")
        lines.append("")
        context.write_text(req / "governance_report.md", "\n".join(lines))
        emit({"ok": True, "file": "governance_report.md", "report": report})
        return
    emit(report)


def verb_ctx(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    pipeline = Pipeline(context.ticket_dir(ROOT, tid))
    sub = flags.get("sub") or flags.get("1", "")
    if sub == "list" or not sub:
        emit(pipeline.state.get("context", {}))
        return
    if sub == "set":
        key = flags.get("key", "")
        value = flags.get("value", "true")
        if not key:
            print("ctx set 需要 --key [--value]", file=sys.stderr)
            sys.exit(2)
        protected = {
            "verify_result",
            "self_check_result",
            "user_confirmed",
            "code_diff_done",
            "code_diff.required",
            "etl_dev_evidence_ok",
            "test_evidence_ok",
            "deploy_executed",
            "tracker_transitioned",
        }
        if key in protected:
            emit(
                {
                    "ok": False,
                    "blocked": True,
                    "gate": "state_write_guard",
                    "reason": f"{key} 是机器证据键，禁止 ctx set 自写；请用 evidence verify|selfcheck|confirm（由真实产物/确认记录驱动）。",
                }
            )
            sys.exit(2)
        if value == "true":
            value = True
        elif value == "false":
            value = False
        elif re.fullmatch(r"-?\d+", str(value)):
            value = int(value)
        pipeline.set_context(key, value)
        emit({"ok": True, "key": key, "value": value})
        return
    print("ctx 子命令: set|list")


def verb_evidence(flags: Dict[str, str]) -> None:
    """Set machine evidence keys only after checking real artifacts / confirmations."""
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    sub = flags.get("sub") or flags.get("1", "")
    if sub == "verify":
        ev = context.load_json(req / "etl_dev_exec.json") or {}
        ok_ev, reason = validators.execution_evidence_ok(ev, CONFIG.get("limits") or {})
        if not ok_ev:
            emit({"ok": False, "blocked": True, "gate": "evidence_verify", "reason": f"etl_dev_exec.json 证据不合格: {reason}"})
            sys.exit(1)
        ledger_path = context.ledger_dir(ROOT) / "executions.json"
        ledger = context.load_json(ledger_path, {"runs": []}) or {"runs": []}
        known_ids = {str(r.get("query_id")) for r in ledger.get("runs", []) if isinstance(r, dict)}
        if str(ev.get("query_id")) not in known_ids:
            emit(
                {
                    "ok": False,
                    "blocked": True,
                    "gate": "evidence_ledger",
                    "reason": "query_id 不在引擎执行账本中：证据必须来自 engine platform run-sql（AI 不能手写执行证据）",
                }
            )
            sys.exit(1)
        pipeline.set_context("verify_result", "passed", log=True)
        emit({"ok": True, "key": "verify_result", "source": "etl_dev_exec.json"})
        return
    if sub == "selfcheck":
        required = pipeline.context_key("code_diff.required") is True
        if required and not (req / "code_diff.html").is_file():
            emit({"ok": False, "blocked": True, "gate": "evidence_selfcheck", "reason": "code_diff.required=true 但 code_diff.html 不存在"})
            sys.exit(1)
        pipeline.set_context("self_check_result", "passed", log=True)
        emit({"ok": True, "key": "self_check_result"})
        return
    if sub == "confirm":
        marker = context.read_text(req / "_user_confirmation.md")
        if "[已确认]" not in marker:
            emit({"ok": False, "blocked": True, "gate": "evidence_confirm", "reason": "缺 _user_confirmation.md 且不含 [已确认]（AskUserQuestion 记录）"})
            sys.exit(1)
        pipeline.set_context("user_confirmed", True, log=True)
        emit({"ok": True, "key": "user_confirmed"})
        return
    print("evidence 子命令: verify|selfcheck|confirm", file=sys.stderr)
    sys.exit(2)


def verb_sql(flags: Dict[str, str]) -> None:
    text = flags.get("text", "")
    if flags.get("file"):
        text = context.read_text(Path(flags["file"]))
    filename = flags.get("file", "") or ""
    if not text:
        print("sql check 需要 --text 或 --file", file=sys.stderr)
        sys.exit(2)
    # 方言：显式 --dialect 优先；否则按配置（platform.kind）推导。
    # 推不出来或未登记一律退回 generic —— 那正是改动前的行为（只跑基线 17 项）。
    dialect = str(flags.get("dialect") or "").strip()
    if not dialect:
        try:
            dialect = engines.dialect_from_config(CONFIG)
        except engines.UnknownEngine as err:
            print(f"[配置] {err}", file=sys.stderr)
            dialect = "generic"
    issues = validators.sql_compliance_check(
        text,
        filename=Path(filename).name,
        is_dp_task=bool(flags.get("task")),
        dialect=dialect,
    )
    emit(
        {
            "ok": not validators.has_blockers(issues),
            "dialect": dialect,
            "issues": validators.issues_to_dicts(issues),
        }
    )
    if validators.has_blockers(issues):
        sys.exit(1)


def verb_platform(flags: Dict[str, str]) -> None:
    """Mirror the configured data-platform CLI surface so hooks/gates see one vocabulary."""
    sub = flags.get("sub", "") or flags.get("1", "")
    name = flags.get("name", "")
    if not sub:
        print("platform 子命令: doctor|run-sql|check-grammar|create-task|read-task|update-task|submit|publish|backfill")
        sys.exit(2)
    if sub in ("doctor", "diagnose"):
        # 配置体检：只判"配没配对"，不判"连不连得上"（连通性只有真跑才知道）。
        report = engines.diagnose_platform(CONFIG.get("platform") or {})
        emit(report)
        if not report.get("ok"):
            sys.exit(1)
        return
    sql_text = flags.get("sql", "")
    if flags.get("file"):
        sql_text = context.read_text(Path(flags["file"]))
    client = platform()
    if sub in ("run-sql", "run_sql", "runSql"):
        # 不能截断 SQL 再送门：截断会切在表达式中间，制造"括号不配对"这类假失败，
        # 也会让长 SQL 的规范检查形同虚设。
        check_gate(f"platform run_sql --sql {sql_text}")
        project = flags.get("project", "dev")
        env = flags.get("env", "dev")
        result = client.run_sql(sql_text, project, env)
        record_execution(sql_text, result)
        if flags.get("save-evidence") or flags.get("save"):
            tid = context.resolve_ticket(ROOT, flags.get("ticket") or flags.get("id"))
            if not tid:
                print("save-evidence 需要 --ticket <id>", file=sys.stderr)
                sys.exit(2)
            req = context.ticket_dir(ROOT, tid)
            evidence = {
                "status": "SUCCESS",
                "query_id": str(result.get("query_id") or ""),
                "env": env,
                "executed_at": str(result.get("executed_at") or result.get("run_at") or context.utcnow()),
                "sql_files": [flags["file"]] if flags.get("file") else [],
            }
            context.write_json(req / "etl_dev_exec.json", evidence)
        emit(result)
    elif sub in ("check-grammar", "check_sql_grammar", "checkSqlGrammar"):
        emit(client.check_grammar(sql_text))
    elif sub in ("create-task", "create_task", "createTask"):
        code = context.read_text(Path(flags["code-file"])) if flags.get("code-file") else flags.get("code", "")
        check_gate(f"platform create_task --name {name} --code {code}")
        emit(client.create_task(name, code, flags.get("schedule", "")))
    elif sub in ("read-task", "read_task", "readTask"):
        check_gate(f"platform read_task --name {name}")
        result = client.read_task(name)
        # 引擎自己读过的也算"已读"：read 账本原先只由 PostToolUse hook 写入，
        # 纯 CLI / 无 hook 的宿主里 update_task_read_guard 永远无法满足。
        if name:
            gates.mark_read(ROOT, name)
        emit(result)
    elif sub in ("update-task", "update_task", "updateTask"):
        code = context.read_text(Path(flags["code-file"])) if flags.get("code-file") else flags.get("code", "")
        check_gate(f"platform update_task --name {name} --code {code}")
        result = client.update_task(name, code)
        # 改现有任务 ⇒ 触发 code_diff 强校验。
        # 之前只有 demo（进程内）会置位 code_diff.required，live 下这道门是休眠的：
        # 现在由"对已存在任务执行 update-task"这个真实事件自动置位。
        tid = context.resolve_ticket(ROOT, flags.get("ticket") or flags.get("id"))
        if tid:
            req_dir = context.ticket_dir(ROOT, tid)
            if Pipeline(req_dir).exists() and not Pipeline(req_dir).context_key("code_diff.required"):
                Pipeline(req_dir).set_context("code_diff.required", True, log=True)
        emit(result)
    elif sub == "submit":
        check_gate(f"platform submit --name {name}")
        emit(client.submit_task(name))
    elif sub == "publish":
        comment = flags.get("comment", "")
        check_gate(f"platform publish --name {name} --comment {comment}")
        emit(client.publish_task(name, comment))
    elif sub == "backfill":
        check_gate(f"platform backfill --name {name} --start {flags.get('start', '')} --end {flags.get('end', '')}")
        emit(client.backfill(name, flags.get("start", ""), flags.get("end", "")))
    else:
        print(f"未知 platform 子命令: {sub}", file=sys.stderr)
        sys.exit(2)


def verb_kb(flags: Dict[str, str]) -> None:
    kb = clients.make_knowledge(CONFIG, ROOT)
    sub = flags.get("sub", "")
    if sub == "search":
        emit(kb.search(flags.get("query", "")))
    elif sub == "read":
        emit({"uri": flags.get("uri", ""), "content": kb.read(flags.get("uri", ""))})
    else:
        print("kb 子命令: search|read")


def verb_diff(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    # 线上基准优先：`baseline capture` 从数据平台读回的 PUBLISHED 任务代码
    baseline = context.ledger_dir(ROOT) / "baseline" / tid
    if not baseline.is_dir():
        baseline = ROOT / context.APP_DIR_NAME / "mock" / "baseline" / tid
    if not baseline.is_dir():
        emit(
            {
                "ok": False,
                "reason": (
                    f"无线上基准 {baseline}（新表场景可跳过）。live 模式请先跑 "
                    "platform baseline capture --ticket <id> 从平台读回已发布任务代码。"
                ),
            }
        )
        sys.exit(1)
    changed = []
    for path in sorted(baseline.rglob("*.sql")):
        rel = path.relative_to(baseline)
        current = req / rel
        if current.is_file():
            old_text, new_text = context.read_text(path), context.read_text(current)
            if old_text != new_text:
                changed.append(str(rel))
                html = spec.render_code_diff_html(old_text, new_text, title=f"code_diff: {rel}")
                context.write_text(req / "code_diff.html", html)
    if not changed:
        context.write_text(req / "code_diff.html", spec.render_code_diff_html("", "", title="code_diff: 无差异"))
    pipeline = Pipeline(req)
    pipeline.set_context("code_diff_done", True)
    emit({"ok": True, "changed": changed, "code_diff_html": (req / "code_diff.html").is_file()})


def verb_review_report(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    html = spec.render_review_report_html(req, len(pipeline.state.get("autopilot_decisions", [])))
    context.write_text(req / "review_report.html", html)
    emit({"ok": True, "file": "review_report.html"})


def verb_baseline(flags: Dict[str, str]) -> None:
    """Capture the published baseline (live mode) for code_diff.

    从数据平台读回当前已发布的任务代码，落到 ``.data-dev/runtime/baseline/<工单>/``，
    之后 ``diff`` 会优先用它做比对（``.data-dev/mock/baseline`` 留给 mock 模式）。
    """
    sub = flags.get("sub", "")
    if sub not in ("capture", "show"):
        print("baseline 子命令: capture | show", file=sys.stderr)
        sys.exit(2)
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    baseline_root = context.ledger_dir(ROOT) / "baseline" / tid
    if sub == "show":
        files = sorted(p.relative_to(baseline_root).as_posix() for p in baseline_root.rglob("*.sql"))
        emit({"ok": True, "baseline_dir": str(baseline_root), "files": files})
        return
    client = platform()
    captured: List[Dict[str, str]] = []
    missing: List[str] = []
    for sql_path in sorted((req / "dp_task").glob("*.sql")):
        name = sql_path.stem
        try:
            task = client.read_task(name)
        except Exception:
            missing.append(name)
            continue
        code = str(task.get("code") or "")
        if not code.strip():
            missing.append(name)
            continue
        target = baseline_root / "dp_task" / f"{name}.sql"
        context.write_text(target, code)
        captured.append({"task": name, "baseline_file": target.relative_to(ROOT).as_posix()})
    if captured:
        pipeline = Pipeline(req)
        if pipeline.exists() and not pipeline.context_key("code_diff.required"):
            pipeline.set_context("code_diff.required", True, log=True)
    emit(
        {
            "ok": bool(captured),
            "baseline_dir": baseline_root.relative_to(ROOT).as_posix(),
            "captured": captured,
            "missing": missing,
        }
    )
    if not captured:
        sys.exit(1)


def verb_deploy(flags: Dict[str, str]) -> None:
    """Deterministic P5 execution after the three confirmation gates."""
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    comment = flags.get("comment", "")
    plan_pre = context.load_json(req / "deploy_plan.json") or {}
    plan_env = str(plan_pre.get("env") or "").lower()
    is_prod = plan_env in ("prod", "production")
    if is_prod and "[已确认发布]" not in comment:
        emit({"ok": False, "blocked": True, "gate": "publish_gate", "reason": "comment 必须含 [已确认发布] token（AskUserQuestion 产物）"})
        sys.exit(1)
    first_table = str((plan_pre.get("items") or [{}])[0].get("table", ""))
    check_gate(f"platform publish --name {first_table} --comment {comment[:80]}")
    plan = context.load_json(req / "deploy_plan.json") or {}
    plan["publish_confirmed"] = True
    context.write_json(req / "deploy_plan.json", plan)
    client = platform()
    sql_kind = str((CONFIG.get("platform") or {}).get("kind") or "cli")
    file_platform = sql_kind in clients.SQL_PLATFORM_KINDS or sql_kind in clients.COMMAND_PLATFORM_KINDS
    results = []
    for item in plan.get("items", []):
        table = str(item.get("table", ""))
        stem = table.split(".")[-1]
        if item.get("action") in ("create", "alter"):
            if file_platform:
                ddl_path = req / "ddl" / f"{stem}.sql"
                code = context.read_text(ddl_path)
                if not code.strip():
                    raise clients.ClientError(f"SQL 平台发布需要 DDL 文件: {ddl_path}")
                results.append({"item": table, "action": item.get("action"), **client.run_sql(code, env=plan_env)})
            else:
                results.append(client.publish_task(table, comment))
        if item.get("action") == "backfill":
            if file_platform:
                task_path = req / "dp_task" / f"{stem}.sql"
                code = context.read_text(task_path)
                if not code.strip():
                    raise clients.ClientError(f"SQL 平台回刷需要 dp_task 文件: {task_path}")
                results.append(
                    client.backfill_code(
                        code,
                        str(item.get("range_start", "")),
                        str(item.get("range_end", "")),
                        task_name=table,
                        env=plan_env,
                    )
                )
            else:
                results.append(client.backfill(table, item.get("range_start"), item.get("range_end")))
    workitem = {}
    stage_result: Dict[str, Any] = {}
    if is_prod:
        workitem = tracker().query(tid)
        stage_result = tracker().set_status(tid, "待验收", reason="P5 生产核实通过，工单逐级流转")
    lines = [
        "# delivery_summary.md",
        "",
        f"- ticket: {tid}",
        f"- phase: 5 运维部署",
        f"- env: {plan.get('env')}",
        f"- plan: {plan.get('env')} / {len(plan.get('items', []))} items",
    ]
    for item in plan.get("items", []):
        lines.append(f"- item: {item.get('action')} {item.get('table')} range={item.get('range_start', '-')}~{item.get('range_end', '-')}")
    rollback_plan = [
        {"table": str(i.get("table")), "action": i.get("action"), "rollback_sql_file": str(i.get("rollback_sql_file", ""))}
        for i in plan.get("items", [])
        if str(i.get("rollback_sql_file", "")).strip()
    ]
    lines.append(f"- 回滚预案: {len(rollback_plan)} 项（可用 ddp.py rollback 执行）" if rollback_plan else "- 回滚预案: 未配置")
    lines += [
        "",
        "## 核实记录",
        "",
        "- 新列/ETL 上线已只读核实（query_id 见 deploy_log.json）",
        ("- 工单状态: " + stage_result.get("status", "待验收")) if is_prod else "- 非生产环境部署：跳过工单流转",
        "",
        "## autopilot 决策",
        "",
        ("部署门前已全文展示 autopilot_decisions.md 与 review_report.html；⚠️ 不可逆门逐项用户确认。"
         if is_prod else "非生产部署：门控记录留存，无需发布确认 token。"),
        "",
        "完成。",
    ]
    context.write_text(req / "delivery_summary.md", "\n".join(lines))
    context.write_json(
        req / "deploy_log.json",
        {
            "deployed_at": context.utcnow(),
            "results": results,
            "stage": stage_result,
            "workitem_status": workitem.get("status"),
            "rollback_plan": rollback_plan,
        },
    )
    if is_prod:
        pipeline.set_context("tracker_transitioned", "待验收")
    pipeline.set_context("deploy_executed", True)
    emit({"ok": True, "results": results, "stage": stage_result})


def verb_rollback(flags: Dict[str, str]) -> None:
    """Execute configured rollback SQL files after a failed/unwanted deployment."""
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    comment = flags.get("comment", "")
    if "[已回滚]" not in comment:
        emit({"ok": False, "blocked": True, "gate": "rollback_gate", "reason": "comment 必须含 [已回滚] token（用户确认记录）"})
        sys.exit(1)
    plan = context.load_json(req / "deploy_plan.json") or {}
    items = [
        i for i in (plan.get("items") or [])
        if str(i.get("rollback_sql_file", "")).strip()
    ]
    if not items:
        emit({"ok": False, "blocked": True, "reason": "deploy_plan 未配置任何 rollback_sql_file，无法回滚"})
        sys.exit(1)
    sql_kind = str((CONFIG.get("platform") or {}).get("kind") or "cli")
    file_platform = sql_kind in clients.SQL_PLATFORM_KINDS or sql_kind in clients.COMMAND_PLATFORM_KINDS
    if not file_platform:
        emit({"ok": False, "blocked": True, "reason": "rollback 只支持 SQL/命令平台（kind=cli 请在平台侧回滚）"})
        sys.exit(1)
    env = str(flags.get("env") or plan.get("env") or "prod")
    client = platform()
    results = []
    for item in items:
        rollback_file = req / str(item.get("rollback_sql_file"))
        code = context.read_text(rollback_file)
        if not code.strip():
            raise clients.ClientError(f"回滚文件缺失或为空: {rollback_file}")
        results.append(
            {
                "table": str(item.get("table")),
                "action": item.get("action"),
                "rollback_sql_file": str(item.get("rollback_sql_file")),
                **client.run_sql(code, env=env),
            }
        )
    context.write_json(
        req / "rollback_log.json",
        {
            "rolled_back_at": context.utcnow(),
            "env": env,
            "comment": comment,
            "results": results,
        },
    )
    pipeline.set_context("rollback_executed", True)
    emit({"ok": True, "env": env, "results": results, "file": "rollback_log.json"})


def verb_semantic(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(req)
    scenario = flags.get("scenario", "metric-dev")
    marker = context.read_text(req / "_user_confirmation.md")
    if "[已确认]" not in marker:
        emit(
            {
                "ok": False,
                "blocked": True,
                "gate": "semantic_entry_gate",
                "reason": "进入语义层需要 _user_confirmation.md 含 [已确认]（用户拍板记录，autopilot 不旁路）",
            }
        )
        sys.exit(1)
    # 摘要必须来自本单真实产物，不能是写死模板（否则每张工单都会带同一个假模型名与假发布声明）
    sds_model = context.load_json(req / "sds_design.json") or {}
    target_name = str((sds_model.get("target_table") or {}).get("name") or "")
    metric_fields = [
        str(item.get("target_field"))
        for item in (sds_model.get("field_mappings") or [])
        if item.get("target_field")
    ]
    knowledge_uri = str(sds_model.get("knowledge_source_uri") or "")
    publish_state = (
        "mock 离线模式未执行（无 OV 可同步）；live 模式需由 knowledge-base 插件发布后人工核验 OV 可见性"
        if str((CONFIG or {}).get("mode") or "mock") != "live"
        else "已委托 knowledge-base 插件发布；引擎侧未核验，请以 kb-publish 产物与 OV 可见性为准"
    )
    lines = [
        "# semantic_update_summary.md",
        "",
        f"- ticket: {tid}",
        f"- 决策: 用户已拍板进入语义层更新（AskUserQuestion=yes，autopilot 不旁路）",
        f"- 场景: {scenario}",
        f"- 目标表: {target_name or '（SDS 未声明）'}",
        f"- 拟登记指标/字段: {'、'.join(metric_fields) if metric_fields else '（SDS 未声明字段映射）'}",
        f"- 知识源: {knowledge_uri or '（SDS 未声明）'}",
        "- yaml: 由 dp-semantic-model 生成 5 类 YAML（本摘要是**登记单**，不代表已生成）",
        "- 发布: 委托 knowledge-base 插件 kb-publish（git push + OV 同步）",
        f"- 发布状态: {publish_state}",
        "",
        "## 更新项",
        "",
        f"1. {scenario} 场景：登记 {target_name or '目标表'} 的指标口径 YAML（字段取自 SDS 字段映射）",
        f"2. 表元数据: {target_name or '目标表'} 资产标签与血缘（引用 sds_design.json 字段映射）",
        f"3. 维度/指标血缘: {f'{len(metric_fields)} 个字段' if metric_fields else '按 SDS 产出'}，与 requirement_spec.md 桥接一致",
        "",
        "## 校验",
        "",
        "- dpsl 语法校验: 未在引擎内执行（由 knowledge-base 插件负责，P6 出口门按 WARN 记录）",
        f"- kb-publish: {publish_state}",
        "",
        "> 本文档只证明「用户拍板已记录 + 语义层登记单已落盘」，不构成语义层已发布的证据；",
        "> 真实发布结果以 knowledge-base 插件（kb-publish）的产物与 OV 可见性为准。",
    ]
    context.write_text(req / "semantic_update_summary.md", "\n".join(lines))
    pipeline.set_context("semantic_decided", True)
    pipeline.set_context("semantic_user_confirmed", True)
    emit({"ok": True, "file": "semantic_update_summary.md"})


def verb_finish(flags: Dict[str, str]) -> None:
    tid = ticket(flags)
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline(context.ticket_dir(ROOT, tid))
    enriched = dict(pipeline.state)
    enrich_state(enriched)
    problems = validators.blockers(
        validators.stage_gate(6, req, enriched, clients.kb_prefix(CONFIG))
    )
    if problems:
        emit({"ok": False, "blocked": True, "issues": validators.issues_to_dicts(problems)})
        sys.exit(1)
    pipeline.finish(flags.get("reason", "六阶段闭环完成"))
    plan = context.load_json(req / "deploy_plan.json") or {}
    if str(plan.get("env") or "").lower() in ("prod", "production"):
        client = tracker()
        try:
            current = client.query(tid).get("status")
            for stage in ("待测试", "待业务验收", "已交付"):
                if current != stage:
                    client.set_status(tid, stage, reason="工单逐级流转（不允许跨 stage 跳跃）")
                    current = stage
        except clients.ClientError as exc:
            emit({"ok": False, "reason": f"工单流转失败: {exc}"})
            sys.exit(1)
    emit(pipeline.summary())


# ---------------------------------------------------------------------------
# demo (mock mode, deterministic happy path)
# ---------------------------------------------------------------------------
def _sample_description() -> str:
    return """## 背景
数仓用户域活跃指标口径在 DWS 层被重复加工，业务侧报表不一致，需要收敛为统一产出并补测试。

## 目标
产出一张用户活跃日快照表 ads_user_act_di，L1 改造现有任务，补齐机器测试证据与部署门。

## 范围
目标表: ads_user_act_di（ads/user）
来源: dwd_user_di（dwd 用户域明细）

## 验收口径
验收: 字段口径与 dwd 明细一致，join_match_rate>=0.995，null_rate=0；P4 报告标✅ 用例全部带 query_id。

## 风险
风险: 改现有任务影响下游报表，需 code_diff 展示与发布确认；回刷按日期范围受控。

负责人: 张三（业务） / 李四（数仓）
计划日期: 2026-09-30
"""


def _sample_sds(ticket_id: str, title: str) -> Dict[str, Any]:
    return {
        "ticket_id": ticket_id,
        "ticket": ticket_id,
        "title": title,
        "mode": "verify",
        "declared_source": "dwd_user_di",
        "level_suggestion": "L1",
        "target_table": {
            "name": "ads_user_act_di",
            "layer": "ads",
            "domain": "user",
            "comment": "用户活跃日快照（L1 改造）",
        },
        "field_mappings": [
            {
                "target_field": "user_id",
                "source_table": "dwd_user_di",
                "source_field": "user_id",
                "source_layer": "dwd",
                "conclusion": "accurate",
                "correction": "",
                "sql_evidence": {
                    "sql_id": "ev1",
                    "join_match_rate": 0.9991,
                    "null_rate": 0.0,
                    "row_count": 1286400,
                    "key_kind": "pk",
                },
            },
            {
                "target_field": "active_flag",
                "source_table": "dwd_user_di",
                "source_field": "active_flag",
                "source_layer": "dwd",
                "conclusion": "accurate",
                "correction": "",
                "sql_evidence": {
                    "sql_id": "ev2",
                    "join_match_rate": 0.9988,
                    "null_rate": 0.0,
                    "row_count": 1286400,
                    "key_kind": "dim",
                },
            },
        ],
        "needs_intermediate_table": False,
        "intermediate_note": "来源为明细层 dwd，无需中间表",
        "knowledge_source_uri": "viking://resources/domain/dwd-user/readme.md",
        "knowledge_gaps": [],
        "knowledge_gap_evidence": [],
        "knowledge_backfill": {"required": False, "resolved": None},
        "sql_validation": {"sql_count": 2, "join_match_rate": 0.9988, "null_rate": 0.0, "row_count": 1286400},
        "verification": {"evidence_json": "verification/evidence.json", "sql_files": ["verification/q1.sql", "verification/q2.sql"]},
        "confirmed": False,
        "final_version": 1,
    }


def _sample_evidence(sds_obj: Dict[str, Any]) -> Dict[str, Any]:
    sql = sds_obj["sql_validation"]
    return {
        "sql_count": sql["sql_count"],
        "join_match_rate": sql["join_match_rate"],
        "null_rate": sql["null_rate"],
        "row_count": sql["row_count"],
        "sql_files": ["q1.sql", "q2.sql"],
        "queries": [
            {"sql_id": "ev1", "query_text_file": "q1.sql"},
            {"sql_id": "ev2", "query_text_file": "q2.sql"},
        ],
    }


def _write_dev_artifacts(req: Path) -> None:
    context.ensure_dir(req / "ddl")
    context.ensure_dir(req / "etl")
    context.ensure_dir(req / "dp_task")
    context.write_text(
        req / "ddl" / "ads_user_act_di.sql",
        "-- table: ads_user_act_di\n-- env: dev\n-- 存量表 L1 改造：新增 active_flag 列（DDL 变更脚本）\nALTER TABLE ads_user_act_di ADD COLUMN active_flag STRING COMMENT '活跃标志';\n",
    )
    context.write_text(
        req / "etl" / "ads_user_act_di.sql",
        "-- table: ads_user_act_di\n-- source: dwd_user_di\n-- env: dev\nINSERT OVERWRITE TABLE ads_user_act_di PARTITION (ds='20260907')\nSELECT user_id, active_flag\nFROM dwd_user_di\nWHERE ds = '20260907';\n",
    )
    context.write_text(
        req / "dp_task" / "ads_user_act_di.sql",
        "-- table: ads_user_act_di\n-- source: dwd_user_di\n-- schedule: 0 2 * * *\n-- env: dev\nINSERT OVERWRITE TABLE ads_user_act_di PARTITION (ds='${bizdate}')\nSELECT user_id, active_flag\nFROM dwd_user_di\nWHERE ds = '${bizdate}';\n",
    )
    context.write_json(
        req / "etl_dev_exec.json",
        {
            "status": "SUCCESS",
            "query_id": "q_dev_ads_user_act",
            "env": "dev",
            "run_at": context.utcnow(),
            "sql_files": ["etl/ads_user_act_di.sql"],
        },
    )


def _write_test_artifacts(req: Path) -> None:
    context.write_text(
        req / "test_report.md",
        """# test_report.md

## 测试用例

| test_id | 用例 | 结果 |
| --- | --- | --- |
| TC-0001 | user_id 主键唯一性 + 活跃口径抽数比对 | ✅ PASS |

## 结论

全部通过，机器证据见 test_runs.json。
""",
    )
    context.write_json(
        req / "test_runs.json",
        {
            "generated_by": "workflow/phase4-test.js",
            "runs": [
                {
                    "test_id": "TC-0001",
                    "description": "user_id 主键唯一性 + 活跃口径抽数比对",
                    "query_id": "q_tc_0001",
                    "status": "SUCCESS",
                    "sql_file": "test/tc_0001.sql",
                    "duration_s": 1.2,
                }
            ],
            "summary": {"total": 1, "success": 1, "failed": 0},
        },
    )


def _write_deploy_artifacts(req: Path, tid: str) -> None:
    context.write_json(
        req / "deploy_plan.json",
        {
            "ticket_id": tid,
            "env": "prod",
            "publish_confirmed": False,
            "items": [
                {"action": "alter", "table": "ads_user_act_di", "ddl_diff": "ddl/ads_user_act_di.sql"},
                {"action": "backfill", "table": "ads_user_act_di", "range_start": "20260901", "range_end": "20260907"},
            ],
        },
    )


def verb_demo(flags: Dict[str, str]) -> None:
    mode = CONFIG.get("mode") or flags.get("mode", "mock")
    if mode != "mock":
        print("demo 只支持 mock 模式（--root 指向临时目录即可）", file=sys.stderr)
        sys.exit(2)
    steps: List[Dict[str, str]] = []
    log: Callable[[str, str], None] = lambda gate, msg: steps.append({"gate": gate, "msg": msg})

    # setup + submit
    verb_setup(flags)
    log("setup", f"项目初始化 mode={mode}")
    title = "【数据需求】用户活跃指标 L1 改造"
    description = _sample_description()
    client = tracker()
    item = client.create_work_item(title, description)
    tid = item["id"]
    req = context.ticket_dir(ROOT, tid)
    pipeline = Pipeline.init(req, tid, title, description, context.sha256_text(description))
    context.set_current_ticket(ROOT, tid)
    log("submit", f"建单 {tid}（submit_quality=passed）")

    # P1 SDS
    sds_obj = _sample_sds(tid, title)
    context.write_json(req / "sds_design.json", sds_obj)
    context.ensure_dir(req / "verification")
    context.write_json(req / "verification" / "evidence.json", _sample_evidence(sds_obj))
    context.write_text(req / "verification" / "q1.sql", "SELECT count(*) FROM dwd_user_di WHERE ds='20260907';\n")
    context.write_text(req / "verification" / "q2.sql", "SELECT count(distinct user_id) FROM ads_user_act_di WHERE ds='20260907';\n")
    issues = validators.validate_sds_design(
        req,
        CONFIG.get("limits") or {},
        require_confirmed=False,
        kb_prefix=clients.kb_prefix(CONFIG),
        modeling=CONFIG.get("modeling") or {},
    )
    if validators.has_blockers(issues):
        emit({"ok": False, "issues": validators.issues_to_dicts(validators.blockers(issues))})
        sys.exit(1)
    log("sds:validate", "八条硬规则 passed（含 evidence 比对）")
    sds_obj["confirmed"] = True  # 确认门：AskUserQuestion=yes（demo 自动确认）
    context.write_json(req / "sds_design.json", sds_obj)
    context.write_text(req / "sds_design_spec.md", spec.render_sds_spec(req))
    payload = spec.build_ticket_comment(req)
    client.add_comment(tid, payload["content_md"], anchor=payload["anchor"])
    latest = spec.verify_latest_comment(client.comments(tid))
    assert latest and latest["verified"]
    log("sds:publish", f"终版贴单 v1 + 回读校验通过（ticket_hash={context.sha256_text(description)[:8]}）")
    spec.bridge_to_spec(req, client.query(tid))
    pipeline = Pipeline(req)
    pipeline.set_level("L1", "SDS 导出难度建议")
    pipeline.sds["ticket_hash"] = context.sha256_text(description)
    pipeline.state["ticket_hash"] = context.sha256_text(description)
    pipeline.sds["confirmed"] = True
    pipeline.sds["final_version"] = 1
    pipeline.set_sds_status(state.SDS_PUBLISHED, "demo 确认门通过（AskUserQuestion=yes）")

    # start -> P2
    flags2 = dict(flags)
    flags2.setdefault("ticket", tid)
    if flags.get("autopilot"):
        flags2["autopilot"] = "1"
    verb_start(flags2)
    pipeline = Pipeline(req)
    log("start", f"准入门 5 步通过 -> P2（{pipeline.state.get('run_mode')}）")

    # P2 architecture
    pipeline = Pipeline(req)
    level = pipeline.level
    context.write_text(
        req / "architecture_doc.md",
        f"""# architecture_doc.md

难度级别: {level}
目标表: ads_user_act_di（ads/user）

## 交付表清单

```json
{{
  "tables": [
    {{"name": "ads_user_act_di", "action": "alter", "layer": "ads"}}
  ]
}}
```

## 链路

```mermaid
graph LR
A[dwd_user_di] --> B[etl 改造]
B --> C[ads_user_act_di]
C --> D[下游报表]
```

## 影响分析

L1 改造：新增 active_flag 列与任务代码，下游 3 张报表需回归。
""",
    )
    context.write_text(
        req / "pipeline.html",
        "<!doctype html><html><head><title>pipeline</title></head><body><h1>链路</h1><p>dwd -> etl -> ads</p></body></html>",
    )
    log("phase2", "architecture_doc.md + pipeline.html 产出")
    _advance_or_die(pipeline, 3, "P2 架构出口门控")
    log("phase2:advance", "stage_gate P2 passed -> P3")

    # P3 data dev
    _write_dev_artifacts(req)
    pipeline = Pipeline(req)
    # baseline modification -> code_diff required
    baseline = ROOT / context.APP_DIR_NAME / "mock" / "baseline" / tid / "dp_task"
    context.write_text(
        baseline / "ads_user_act_di.sql",
        "-- table: ads_user_act_di\n-- source: dwd_user_di\n-- schedule: 0 2 * * *\n-- env: dev\nINSERT OVERWRITE TABLE ads_user_act_di PARTITION (ds='${bizdate}')\nSELECT user_id FROM dwd_user_di WHERE ds='${bizdate}';\n",
    )
    pipeline.set_context("code_diff.required", True)
    _diff_and_report(req, tid)
    pipeline.set_context("verify_result", "passed")
    pipeline.set_context("self_check_result", "passed")
    pipeline.set_context("user_confirmed", True)
    pipeline.set_context("code_diff_done", True)
    log("phase3", "ddl/etl/dp_task + dev 执行证据 + self_check + code_diff")
    # 注册 platform 任务（真实流程：SQL 验证 -> self_check -> 用户确认 -> create/update）
    dp_client = platform()
    for sql_path in sorted((req / "dp_task").glob("*.sql")):
        name = sql_path.stem
        code = context.read_text(sql_path)
        try:
            dp_client.read_task(name)
            dp_client.update_task(name, code)
        except clients.ClientError:
            dp_client.create_task(name, code, schedule="0 2 * * *")
        dp_client.submit_task(name)
    _advance_or_die(pipeline, 4, "P3 出口（delivery_checklist + stage_gate）")
    log("phase3:advance", "delivery_checklist + stage_gate P3 passed -> P4")

    # P4 tests
    _write_test_artifacts(req)
    pipeline = Pipeline(req)
    log("phase4", "test_report.md + test_runs.json（✅ TC 均带 query_id）")
    _advance_or_die(pipeline, 5, "P4 机器证据门")
    log("phase4:advance", "stage_gate P4 passed -> P5")

    # P5 deploy: three confirmation gates (发布/ALTER/回刷) — engine records them
    _write_deploy_artifacts(req, tid)
    pipeline = Pipeline(req)
    pipeline.enter_confirmation("P5 发布/ALTER/回刷确认")
    pipeline.decide("P5 发布", "发布 ads_user_act_di（ALTER 加列 + 回刷 20260901~20260907）", risk=True)
    pipeline.leave_confirmation()
    if pipeline.state.get("run_mode") == "autopilot":
        context.write_text(
            req / "review_report.html",
            spec.render_review_report_html(req, len(pipeline.state.get("autopilot_decisions", []))),
        )
    if not (req / "code_diff.html").is_file():
        _diff_and_report(req, tid)
    verb_deploy({**flags, "ticket": tid, "comment": "[已确认发布] AskUserQuestion=yes 三道部署门全部确认"})
    log("phase5", "deploy_phase5.js 确定性执行：publish+backfill+工单流转待验收+delivery_summary")
    pipeline = Pipeline(req)
    _advance_or_die(pipeline, 6, "P5 出口")
    log("phase5:advance", "stage_gate P5 passed -> P6")

    # P6 semantic (user-gated entry)
    pipeline = Pipeline(req)
    pipeline.enter_confirmation("P6 是否进入语义层更新（用户拍板）")
    pipeline.decide("P6 语义层", "用户确认进入语义层更新，委托 knowledge-base 插件", risk=True)
    pipeline.leave_confirmation()
    context.write_text(req / "_user_confirmation.md", "[已确认] 用户拍板进入语义层更新（demo AskUserQuestion=yes）")
    verb_semantic({**flags, "ticket": tid, "scenario": "metric-dev"})
    log("phase6", "semantic_update_summary.md（用户拍板，非 AI 自决）")
    pipeline = Pipeline(req)
    pipeline.set_context("semantic_entry_user_decided", True)
    verb_finish({**flags, "ticket": tid, "reason": "从建单到语义层闭环"})
    log("finish", "P6 出口 passed，流水线闭环（工单 -> 已交付）")

    emit({"ok": True, "ticket": tid, "run_mode": pipeline.state.get("run_mode"), "steps": steps})


def _diff_and_report(req: Path, tid: str) -> None:
    flags = {"ticket": tid}
    verb_diff(flags)


def _advance_or_die(pipeline: Pipeline, target: int, reason: str) -> None:
    def gate(current_phase: int) -> List[Any]:
        enriched = dict(pipeline.state)
        enrich_state(enriched)
        return validators.stage_gate(current_phase, pipeline.directory, enriched, clients.kb_prefix(CONFIG))

    result = pipeline.advance(target, reason, gate=gate)
    if not result.get("ok"):
        emit({"ok": False, "result": result})
        sys.exit(1)


# ---------------------------------------------------------------------------
HELP = """data-development-plugin engine

verbs:
  setup [--mode mock|live]
  ticket create --title ... --description ... | --description-file <f> [--force]
  ticket show|comments|verify-latest [--ticket <id>]
  comment add --ticket <id> --anchor <a> --content ... | --file <f>
  sds enter --mode verify|generate        # P1 入口（状态机 enter_sds）
  sds validate [--confirm] | render | build-comment | bridge | backfill-resolve --action synced|declined | review --action refresh|supply
  start [--ticket <id>] [--autopilot]        # 准入门 5 步 -> P2
  advance --phase N --ticket <id> --reason ... [--allow-back]
  status [--ticket <id>]
  status --gate [--ticket <id>]            # 诊断当前阶段出口门
  report [--ticket <id>] [--write]          # 治理摘要（时间线+证据+决策）
  engine sync --from <plugin-engine-root>  # /setup 用：同步引擎副本
  ctx set --key k [--value v] | ctx list      # 机器证据键除外，只能用 evidence
  evidence verify|selfcheck|confirm [--ticket <id>]   # 证据键只能由此写
  sql check --text '...' | --file x.sql [--task] [--dialect <hive|spark|doris|clickhouse|...>]
  platform doctor                            # 配置体检：kind/必填键/profiles/明文口令
  platform run-sql|check-grammar|create-task|read-task|update-task|submit|publish|backfill ...
  kb search --query ... | read --uri viking://resources/...
  baseline capture|show [--ticket <id>]       # live：从平台读回已发布任务代码作线上基准
  diff [--ticket <id>]                        # 比对线上基准（无则 mock baseline）-> code_diff.html
  review-report [--ticket <id>]
  deploy --comment '[已确认发布] ...' [--ticket <id>]
  rollback --comment '[已回滚] ...' [--ticket <id>] [--env prod]
  semantic [--ticket <id>] [--scenario metric-dev]
  finish [--ticket <id>]
  demo [--autopilot] [--root <tmp>]

global: --root <project-dir>  (default: nearest .data-dev root or cwd)
"""


def __verbs_help() -> str:
    return HELP


def main() -> None:
    tokens = sys.argv[1:]
    if not tokens or tokens[0] in ("--help", "-h", "help"):
        print(HELP)
        return
    known_verbs = {
        "setup", "engine", "ticket", "comment", "sds", "start", "advance", "status", "report", "ctx",
        "evidence", "sql", "platform", "kb", "diff", "review-report", "deploy", "semantic",
        "rollback", "finish", "demo", "baseline",
    }
    verb_index = next((i for i, token in enumerate(tokens) if token in known_verbs), None)
    if verb_index is None:
        print(HELP)
        return
    verb = tokens[verb_index]
    flags = boot(tokens)
    sub = next((token for token in tokens[verb_index + 1 :] if not token.startswith("-")), None)
    if sub and verb in (
        "ticket", "sds", "kb", "platform", "ctx", "evidence", "comment", "sql", "engine", "baseline",
    ):
        flags["sub"] = sub
    handlers: Dict[str, Callable[[Dict[str, str]], None]] = {
        "setup": verb_setup,
        "engine": verb_engine,
        "ticket": verb_ticket,
        "comment": verb_comment,
        "sds": verb_sds,
        "start": verb_start,
        "advance": verb_advance,
        "status": verb_status,
        "report": verb_report,
        "ctx": verb_ctx,
        "evidence": verb_evidence,
        "sql": verb_sql,
        "platform": verb_platform,
        "kb": verb_kb,
        "diff": verb_diff,
        "review-report": verb_review_report,
        "deploy": verb_deploy,
        "rollback": verb_rollback,
        "semantic": verb_semantic,
        "finish": verb_finish,
        "baseline": verb_baseline,
        "demo": verb_demo,
    }
    handler = handlers.get(verb)
    if handler is None:
        print(f"未知命令: {verb}\n{HELP}", file=sys.stderr)
        sys.exit(2)
    handler(flags)


if __name__ == "__main__":
    main()
