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

DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 3600
# 工单号/目标表名会被写进知识库产物路径，这里只放行“安全标识符”形态，
# 挡住 ../ 这类路径穿越、空串和以点开头的奇怪值。
_SAFE_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``knowledge.auto_update`` 归一化成可用配置。

    返回 ``{"ok": bool, "errors": [str], "config": {...}}``。非法值不抛异常——
    调用方（``finish``）要拿到“配置错在哪”，而不是一个裸 traceback。
    """
    errors: List[str] = []
    knowledge = config.get("knowledge")
    if knowledge is None:
        knowledge = {}
    if not isinstance(knowledge, dict):
        return {"ok": False, "errors": ["knowledge 必须是对象"], "config": {}}
    auto = knowledge.get("auto_update")
    if auto is None:
        auto = {}
    if not isinstance(auto, dict):
        return {"ok": False, "errors": ["knowledge.auto_update 必须是对象"], "config": {}}

    enabled = auto.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append("knowledge.auto_update.enabled 必须是布尔值（true/false）")
        enabled = True

    paths: Dict[str, str] = {}
    for key in ("kbp_root", "kbp_engine"):
        value = auto.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            errors.append(f"knowledge.auto_update.{key} 必须是字符串路径")
            value = ""
        paths[key] = value.strip()

    timeout_s = DEFAULT_TIMEOUT_S
    raw_timeout = auto.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int):
        text = "" if raw_timeout is None else str(raw_timeout).strip()
        if text.isdigit():
            raw_timeout = int(text)
        else:
            errors.append("knowledge.auto_update.timeout_s 必须是整数秒")
            raw_timeout = DEFAULT_TIMEOUT_S
    if raw_timeout < 1 or raw_timeout > MAX_TIMEOUT_S:
        errors.append(
            f"knowledge.auto_update.timeout_s 必须在 1..{MAX_TIMEOUT_S} 之间（当前 {raw_timeout}）"
        )
    else:
        timeout_s = int(raw_timeout)

    return {
        "ok": not errors,
        "errors": errors,
        "config": {"enabled": enabled, **paths, "timeout_s": timeout_s},
    }


def safe_ident(value: Any) -> Optional[str]:
    """返回可安全用于产物路径的标识符；不安全时返回 ``None``。"""
    text = str(value or "").strip()
    if not text or text in {".", ".."} or not _SAFE_IDENT.match(text):
        return None
    return text


def _mapping_items(sds: Dict[str, Any]) -> Dict[str, Any]:
    """SDS 是模型/人写的 JSON：字段映射不保证是对象数组，坏条目跳过并留痕。"""
    mappings = sds.get("field_mappings") or []
    if not isinstance(mappings, list):
        return {"items": [], "warnings": ["field_mappings 不是数组"]}
    items: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for idx, item in enumerate(mappings):
        if not isinstance(item, dict):
            warnings.append(f"field_mappings[{idx}] 不是对象，已跳过")
            continue
        items.append(item)
    return {"items": items, "warnings": warnings}



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
    if not isinstance(sds, dict):
        return {"ok": False, "gate": "knowledge_sds_invalid", "reason": "sds_design.json 不是对象"}
    target = sds.get("target_table") or {}
    if not isinstance(target, dict):
        return {"ok": False, "gate": "knowledge_sds_invalid", "reason": "SDS 的 target_table 不是对象"}
    table_id = safe_ident(str(target.get("name") or "").split(".")[-1])
    if not table_id:
        return {
            "ok": False,
            "gate": "knowledge_unsafe_ident",
            "reason": "SDS 目标表名不是安全标识符（只允许字母数字与 ._-，且不能以点开头）",
        }
    domain = _domain(target.get("domain"))
    description = str(state.get("description_md") or "")
    sections = _sections(description)
    mapping_scan = _mapping_items(sds)
    mappings = mapping_scan["items"]
    mapping_warnings = list(mapping_scan["warnings"])
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
        evidence = item.get("sql_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
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
        # 这两个是**功能性字段**，不是展示用元数据：知识库插件拿 artifact_root 定位任务产物、
        # 拿 consumer_root 决定消费索引同步到哪个项目。必须保持绝对路径（实测改成相对路径会
        # 让 ingest 找不到产物、消费索引也同步错位置）。
        "artifact_root": str(req),
        "consumer_root": str(root),
    }
    if mapping_warnings:
        request["mapping_warnings"] = mapping_warnings
    return {"ok": True, "request": request, "request_path": str(req / "knowledge_update_request.json")}


def _write_result(req: Path, result: Dict[str, Any]) -> Dict[str, Any]:
    context.write_json(req / "knowledge_update_result.json", result)
    return result


def submit(root: Path, ticket: str) -> Dict[str, Any]:
    """Best-effort 自动回写；任何失败只落结果，不影响工单已完成的结论。"""
    req = context.ticket_dir(root, ticket)
    normalized = normalize_config(context.load_config(root))
    if not normalized["ok"]:
        return _write_result(
            req,
            {
                "ok": False,
                "gate": "knowledge_config_invalid",
                "reason": "knowledge.auto_update 配置非法：" + "；".join(normalized["errors"]),
                "errors": normalized["errors"],
            },
        )
    auto = normalized["config"]
    if not auto["enabled"]:
        return _write_result(
            req,
            {"ok": False, "skipped": True, "gate": "knowledge_auto_update_disabled",
             "reason": "knowledge.auto_update.enabled=false"},
        )
    if safe_ident(ticket) is None:
        return _write_result(
            req,
            {"ok": False, "gate": "knowledge_unsafe_ident",
             "reason": "工单号不是安全标识符，拒绝写入知识库"},
        )
    built = build_request(root, ticket)
    if not built.get("ok"):
        return _write_result(req, built)
    request = built["request"]
    request_path = Path(built["request_path"])
    context.write_json(request_path, request)

    kbp_root = auto["kbp_root"]
    if not kbp_root and (root / ".kb" / "config.json").is_file():
        kbp_root = str(root)
    engine_path = auto["kbp_engine"]
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
                "kbp_root": kbp_root,
                "timeout_s": auto["timeout_s"],
            },
        )
    timeout = auto["timeout_s"]
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
                "kbp_engine": engine_path,
                "timeout_s": timeout,
            },
        )
    payload: Dict[str, Any] = {}
    try:
        parsed = json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}
        if isinstance(parsed, dict):
            payload = parsed
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
                "kbp_engine": engine_path,
                "timeout_s": timeout,
            },
        )
    payload["request"] = str(request_path)
    # ok=true 但没有任何回执（版本号/URI/对象数）时不能当成"已入库"，
    # 打个标记让调用方与报告都能看出这次是"说成功但拿不出凭证"。
    receipt_keys = ("version", "uri", "uris", "objects", "published_version", "ov_sync")
    if not any(key in payload for key in receipt_keys):
        payload["receipt_missing"] = True
        payload.setdefault("gate", "knowledge_ingest_no_receipt")
    return _write_result(req, payload)
