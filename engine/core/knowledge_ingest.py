"""任务完成后的自动知识回写。

离线插件只负责把“本次任务已经确认的产物”整理成一张知识卡，然后调用
knowledge-base-plugin 的幂等 ingest 接口。知识库是唯一写入方；这里不直接写
viking://，也不因为知识回写失败而回滚已经完成的开发任务。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import context


def _sections(text: str) -> Dict[str, str]:
    wanted = ("背景", "目标", "范围", "验收口径", "风险")
    aliases = {"验收": "验收口径", "验收标准": "验收口径"}
    collected: Dict[str, List[str]] = {name: [] for name in wanted}
    current: Optional[str] = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            title = aliases.get(title, title)
            current = title if title in wanted else None
            continue
        if current and line:
            collected[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in collected.items()}


def _owner(description: str) -> str:
    match = re.search(r"负责人\s*[:：]\s*([^\n]+)", str(description or ""))
    return match.group(1).strip() if match else "未提供"


def _domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text and all(ord(ch) < 128 and (ch.isalnum() or ch in "_-") for ch in text):
        return text
    return "user"


def _ddl_types(ddl: str) -> Dict[str, str]:
    types: Dict[str, str] = {}
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z][A-Za-z0-9_]*(?:\s*\([^)]*\))?)", ddl or ""):
        column, data_type = match.group(1), match.group(2)
        if column.upper() not in {"TABLE", "COLUMN", "PARTITION", "COMMENT", "SELECT", "FROM", "WHERE", "ALTER", "ADD"}:
            types.setdefault(column, data_type)
    return types


def _schedule(text: str) -> str:
    match = re.search(r"--\s*schedule\s*:\s*(.+)", text or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def build_request(root: Path, ticket: str) -> Dict[str, Any]:
    req = context.ticket_dir(root, ticket)
    state = context.load_json(req / "_state.json", {}) or {}
    if not state:
        return {"ok": False, "gate": "knowledge_ticket_missing", "reason": "工单状态不存在"}
    if not state.get("finished"):
        return {"ok": False, "gate": "knowledge_not_finished", "reason": "工单尚未完成，不写知识库"}

    sds = context.load_json(req / "sds_design.json", {}) or {}
    target = sds.get("target_table") or {}
    table_id = str(target.get("name") or "").split(".")[-1].strip()
    if not table_id:
        return {"ok": False, "gate": "knowledge_no_target", "reason": "SDS 未声明目标表"}
    domain = _domain(target.get("domain"))
    description = str(state.get("description_md") or "")
    sections = _sections(description)
    mappings = sds.get("field_mappings") or []
    ddl_text = context.read_text(req / "ddl" / f"{table_id}.sql")
    types = _ddl_types(ddl_text)
    columns = []
    for idx, item in enumerate(mappings):
        field = str(item.get("target_field") or "").strip()
        if not field:
            continue
        columns.append(
            {
                "column": field,
                "data_type": types.get(field, "未知"),
                "comment": f"来源 {item.get('source_table')}.{item.get('source_field')}；结论 {item.get('conclusion')}",
                "is_primary_key": idx == 0 or str((item.get("sql_evidence") or {}).get("key_kind") or "") == "pk",
            }
        )

    calibers: List[str] = []
    for item in mappings:
        evidence = item.get("sql_evidence") or {}
        calibers.append(
            f"{item.get('target_field')} 来自 {item.get('source_table')}.{item.get('source_field')}，"
            f"结论 {item.get('conclusion')}，join_match_rate={evidence.get('join_match_rate')}，"
            f"null_rate={evidence.get('null_rate')}"
        )
    if sds.get("knowledge_source_uri"):
        calibers.append(f"知识源 {sds.get('knowledge_source_uri')}")
    business_context = "\n".join(
        part for part in (sections.get("背景"), sections.get("目标")) if part
    ).strip() or str(state.get("title") or target.get("comment") or table_id)
    acceptance = sections.get("验收口径") or "见工单验收口径。"
    source_tables = sorted(
        {
            str(item.get("source_table"))
            for item in mappings
            if item.get("source_table")
        }
    )
    quality_rules = []
    if columns:
        quality_rules.append({"rule": "pk_unique", "column": columns[0]["column"]})
    quality_rules.append({"rule": "row_count_fluctuation", "column": "*"})
    task_text = context.read_text(req / "dp_task" / f"{table_id}.sql")
    artifacts = [
        name
        for name in (
            "sds_design.json",
            "architecture_doc.md",
            "test_runs.json",
            "test_report.md",
            "deploy_log.json",
            "deploy_plan.json",
            "delivery_summary.md",
            "semantic_update_summary.md",
        )
        if (req / name).is_file()
    ]
    request = {
        "source_plugin": "data-development-plugin",
        "task_id": ticket,
        "task_status": "finished",
        "domain": domain,
        "table_id": table_id,
        "basic_info": {
            "owner": _owner(description),
            "layer": str(target.get("layer") or ""),
            "domain": domain,
            "comment": str(target.get("comment") or state.get("title") or table_id),
        },
        "business_context": business_context,
        "columns": columns,
        "calibers": calibers or ["本次任务未声明字段映射。"],
        "ddl_sql": ddl_text or "-- 本次自动收录未提供 DDL",
        "faq": [
            {"q": "这张表解决什么问题？", "a": business_context[:300]},
            {"q": "验收口径是什么？", "a": acceptance[:300]},
        ],
        "quality_rules": quality_rules,
        "keywords": [table_id, domain] + source_tables,
        "schedule": {"task_name": table_id, "cron": _schedule(task_text)},
        "partition": {"partition_column": "ds", "type": "daily", "retention_days": 30},
        "source_artifacts": artifacts,
        "artifact_root": str(req),
        "consumer_root": str(root),
    }
    return {"ok": True, "request": request, "request_path": str(req / "knowledge_update_request.json")}


def _write_result(req: Path, result: Dict[str, Any]) -> Dict[str, Any]:
    context.write_json(req / "knowledge_update_result.json", result)
    return result


def submit(root: Path, ticket: str) -> Dict[str, Any]:
    """Best-effort 自动回写；任何失败只落结果，不影响工单已完成的结论。"""
    req = context.ticket_dir(root, ticket)
    config = context.load_config(root)
    auto = ((config.get("knowledge") or {}).get("auto_update") or {})
    if not auto.get("enabled", True):
        return _write_result(
            req,
            {"ok": False, "skipped": True, "gate": "knowledge_auto_update_disabled",
             "reason": "knowledge.auto_update.enabled=false"},
        )
    built = build_request(root, ticket)
    if not built.get("ok"):
        return _write_result(req, built)
    request = built["request"]
    request_path = Path(built["request_path"])
    context.write_json(request_path, request)

    kbp_root = str(auto.get("kbp_root") or "").strip()
    if not kbp_root and (root / ".kb" / "config.json").is_file():
        kbp_root = str(root)
    engine_path = str(auto.get("kbp_engine") or "").strip()
    if not engine_path and kbp_root:
        candidate = Path(kbp_root) / ".kb" / "engine" / "kbp.py"
        if not candidate.is_file():
            candidate = Path(kbp_root) / "engine" / "kbp.py"
        if candidate.is_file():
            engine_path = str(candidate)
    if not kbp_root or not engine_path or not Path(engine_path).is_file():
        return _write_result(
            req,
            {
                "ok": False,
                "gate": "knowledge_kb_missing",
                "reason": "未找到知识库项目或 kbp 引擎；请在 knowledge.auto_update 配置 kbp_root/kbp_engine",
                "request": str(request_path),
            },
        )
    timeout = int(auto.get("timeout_s") or 120)
    try:
        proc = subprocess.run(
            [sys.executable, engine_path, "ingest", "--root", kbp_root, "--request", str(request_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _write_result(
            req,
            {
                "ok": False,
                "gate": "knowledge_ingest_exec",
                "reason": f"{type(exc).__name__}: {exc}",
                "request": str(request_path),
            },
        )
    payload: Dict[str, Any] = {}
    try:
        payload = json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if proc.returncode != 0 or not payload.get("ok"):
        return _write_result(
            req,
            {
                "ok": False,
                "gate": payload.get("gate") or "knowledge_ingest_failed",
                "reason": payload.get("reason") or "知识库 ingest 返回失败",
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-2000:],
                "stderr_tail": proc.stderr[-2000:],
                "request": str(request_path),
            },
        )
    payload["request"] = str(request_path)
    return _write_result(req, payload)
