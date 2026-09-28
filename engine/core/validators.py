"""Machine-checkable validators for the data-development-plugin.

Every gate in the pipeline is backed by one of these pure functions. They never
trust what the AI *says*; they only read files on disk and compare values.

Severity model:
  HALTED  -> evidence contradiction: stop and ask a human (SDS rule 6)
  FAIL    -> phase exit gate denies advancement / tool call is blocked
  WARN    -> surfaced as context, never blocks alone

Phase exit gates are aggregated by :func:`stage_gate`.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import context, dialects, engines, formats, schedulers

LEVELS = ("L0", "L1", "L2", "L3")
SUMMARY_LAYERS = ("dws", "ads", "app")
DETAIL_LAYERS = ("ods", "dwd", "dim", "mid")

SDS_REQUIRED_TARGET_KEYS = ("name", "layer", "domain", "comment")
SDS_REQUIRED_MAPPING_KEYS = (
    "target_field",
    "source_table",
    "source_field",
    "source_layer",
    "conclusion",
    "sql_evidence",
)


@dataclass
class Issue:
    code: str
    phase: int
    severity: str  # HALTED | FAIL | WARN
    message: str
    rule: Optional[int] = None
    file: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        where = f" @{self.file}" if self.file else ""
        return f"[{self.severity}] {self.code}{where}: {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "phase": self.phase,
            "severity": self.severity,
            "rule": self.rule,
            "message": self.message,
            "file": self.file,
            "detail": self.detail,
        }


def issues_to_dicts(issues: Iterable[Issue]) -> List[Dict[str, Any]]:
    return [i.to_dict() for i in issues]


def has_blockers(issues: Iterable[Issue]) -> bool:
    return any(i.severity in ("FAIL", "HALTED") for i in issues)


def blockers(issues: Iterable[Issue]) -> List[Issue]:
    return [i for i in issues if i.severity in ("FAIL", "HALTED")]


def _nonempty_markdown(path: Path) -> bool:
    text = context.read_text(path).strip()
    return len(text) >= 10


def execution_evidence_ok(ev: Any, limits: Optional[Dict[str, Any]] = None) -> tuple:
    """Check an execution receipt: status/query_id/env/freshness. Returns (ok, reason)."""
    limits = limits or {}
    if not isinstance(ev, dict):
        return False, "etl_dev_exec.json 缺失或非法"
    if ev.get("status") != "SUCCESS" or not str(ev.get("query_id", "")).strip():
        return False, "缺 SUCCESS + 非空 query_id 真实执行证据"
    if str(ev.get("env", "")).lower() not in ("dev", "development", "staging"):
        return False, "执行证据 env 必须是 dev/development/staging"
    executed_at = str(ev.get("executed_at") or ev.get("run_at") or "").strip()
    if not executed_at:
        return False, "执行证据缺 executed_at/run_at 时间锚点"
    try:
        parsed = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return False, f"执行证据时间无法解析: {executed_at!r}"
    max_hours = float(limits.get("evidence_max_age_hours", 24) or 24)
    age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0
    if age_hours > max_hours:
        return False, f"执行证据已过期（{age_hours:.1f}h > {max_hours:g}h），需重新真跑"
    return True, ""


def _schedule_issues(
    annotations: Dict[str, str], phase: int, filename: str, scheduler: Optional[str]
) -> List[Issue]:
    """第 8 条规则的调度目标变体（COMPAT-010）。

    默认目标 cron：只要有非空 `-- schedule:` 就通过——与改动前的行为逐字一致。
    none：项目没有调度器，不要求注解；写了注解则 WARN（提醒没人消费它）。
    其他目标：缺必填注解 FAIL，缺建议注解 WARN。
    """
    issues: List[Issue] = []
    try:
        spec = schedulers.resolve(scheduler)
    except ValueError as err:
        issues.append(
            Issue("sql.compliance.scheduler_unknown", phase, "FAIL", str(err), rule=8, file=filename)
        )
        return issues

    schedule = str(annotations.get("schedule") or "").strip()
    if spec.kind == "none":
        if schedule:
            issues.append(
                Issue(
                    "sql.compliance.schedule_unused",
                    phase,
                    "WARN",
                    "scheduler.kind=none（无调度器）：这条 -- schedule: 注解不会被任何调度消费",
                    rule=8,
                    file=filename,
                )
            )
        return issues

    if not schedule:
        issues.append(
            Issue(
                "sql.compliance.no_schedule",
                phase,
                "FAIL",
                "dp_task 周期任务缺 -- schedule: 注解",
                rule=8,
                file=filename,
            )
        )
        return issues

    for key in spec.requires:
        if not str(annotations.get(key) or "").strip():
            issues.append(
                Issue(
                    f"sql.compliance.schedule_required.{key}",
                    phase,
                    "FAIL",
                    f"{spec.label} 需要 -- {key}: 注解（{spec.note}）",
                    rule=8,
                    file=filename,
                )
            )
    for key in spec.recommends:
        if not str(annotations.get(key) or "").strip():
            issues.append(
                Issue(
                    f"sql.compliance.schedule_recommended.{key}",
                    phase,
                    "WARN",
                    f"{spec.label} 建议补 -- {key}: 注解（{spec.note}）",
                    rule=8,
                    file=filename,
                )
            )
    return issues


def evidence_level_issues(ev: Any, phase: int, file: str) -> List[Issue]:
    """执行回执的"证据级别"标注（COMPAT-005）。

    回执里的 query_id 有四种来源：平台服务端返回、本机适配器自造、mock 模拟、以及改版前的
    无标注产物。四种都能过门（门保证的是"有 SUCCESS + query_id + 时效 + 账本命中"），
    但**级别不同不该长得一样**——报告里必须看得出这条证据到底是什么。这里统一产出 WARN（不阻断），
    让级别出现在每次出口门的结论里，而不是等出货后才发现"证据强度悄悄降级了"。
    """
    if not isinstance(ev, dict):
        return []
    level = str(ev.get("evidence_level") or "").strip()
    engine = str(ev.get("engine") or "?").strip()
    if level == engines.EVIDENCE_SERVER:
        return []
    if level == engines.EVIDENCE_LOCAL:
        return [
            Issue(
                "evidence.level.local_receipt",
                phase,
                "WARN",
                f"执行回执是本地回执（engine={engine}）：只证明这条 SQL 在本机执行过，"
                "没有平台侧 query_id 可回查",
                file=file,
            )
        ]
    if level == engines.EVIDENCE_MOCK:
        return [
            Issue(
                "evidence.level.mock",
                phase,
                "WARN",
                "执行回执来自 mock 模式：不构成真实执行证据（接真实引擎后重跑一次）",
                file=file,
            )
        ]
    return [
        Issue(
            "evidence.level.unlabeled",
            phase,
            "WARN",
            "执行回执未标注 evidence_level（改版前产物）：重跑一次 platform run-sql 即可带上级别",
            file=file,
        )
    ]


def _ledger_runs(req_dir: Path, name: str) -> Optional[List[Dict[str, Any]]]:
    """``runs`` list from ``.data-dev/runtime/<name>``, or None when no ledger exists.

    Walks up from the ticket directory because the runtime ledger lives next to
    ``.data-dev/``, not inside the ticket.
    """
    cursor = Path(req_dir).resolve()
    while cursor.parent != cursor:
        ledger_path = cursor / context.APP_DIR_NAME / context.LEDGER_DIR / name
        if ledger_path.is_file():
            data = context.load_json(ledger_path, {"runs": []}) or {"runs": []}
            runs = data.get("runs")
            return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []
        cursor = cursor.parent
    return None


def execution_ledger_ids(req_dir: Path) -> Optional[set]:
    """query_id set from .data-dev/runtime/executions.json, or None when no ledger."""
    runs = _ledger_runs(req_dir, "executions.json")
    if runs is None:
        return None
    return {str(r.get("query_id")) for r in runs}


def ledger_sql_hash(req_dir: Path, query_id: str) -> Optional[str]:
    """Content fingerprint the engine recorded for ``query_id``, or None if unknown."""
    if not str(query_id).strip():
        return None
    for run in _ledger_runs(req_dir, "executions.json") or []:
        if str(run.get("query_id")) == str(query_id):
            recorded = str(run.get("sql_hash") or "").strip()
            return recorded or None
    return None


def current_sql_hashes(req_dir: Path, subs: Tuple[str, ...] = ("etl", "dp_task")) -> Dict[str, str]:
    """``{sha256 of file content: relative name}`` for the ticket's SQL deliverables."""
    out: Dict[str, str] = {}
    for sub in subs:
        base = req_dir / sub
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.sql")):
            out[context.sha256_text(context.read_text(path))] = f"{sub}/{path.name}"
    return out


def evidence_binds_to_current_sql(
    req_dir: Path, exec_ev: Any, phase: int, issues: List[Issue], file: str = "etl_dev_exec.json"
) -> None:
    """An execution receipt only proves a run of *some* SQL.

    Require the fingerprint recorded in the engine ledger to still match the
    current ``etl/`` / ``dp_task/`` content, so editing the SQL after passing a
    gate forces a fresh real run instead of reusing the old receipt.
    """
    if not isinstance(exec_ev, dict):
        return
    recorded = ledger_sql_hash(req_dir, str(exec_ev.get("query_id") or ""))
    if not recorded:
        issues.append(
            Issue(
                "evidence.binding.no_hash",
                phase,
                "WARN",
                "执行账本缺 sql_hash（旧账本），无法校验证据与当前 SQL 是否同一份",
                file=file,
            )
        )
        return
    current = current_sql_hashes(req_dir)
    if not current:
        return
    if recorded not in current:
        issues.append(
            Issue(
                "evidence.binding.stale",
                phase,
                "FAIL",
                "证据与当前 etl/dp_task 内容不一致：该 query_id 跑的是另一份 SQL。"
                "过门后改过代码必须重新真跑（engine platform run-sql --save-evidence）",
                file=file,
            )
        )


# ======================================================================
# 1. SDS design validator (documented as "七+一" -> eight rules)
# ======================================================================
def _rule1_structure(sds: Dict[str, Any], issues: List[Issue]) -> None:
    target = sds.get("target_table")
    if not isinstance(target, dict):
        issues.append(Issue("sds.structure.target", 1, "FAIL", "缺 target_table 对象", rule=1))
        return
    missing = [k for k in SDS_REQUIRED_TARGET_KEYS if not str(target.get(k, "")).strip()]
    if missing:
        issues.append(Issue("sds.structure.target_keys", 1, "FAIL", f"target_table 缺必填键: {missing}", rule=1))

    mappings = sds.get("field_mappings")
    if not isinstance(mappings, list) or not mappings:
        issues.append(Issue("sds.structure.mappings", 1, "FAIL", "field_mappings 为空：必须逐字段给出映射", rule=1))
        return
    for idx, mp in enumerate(mappings):
        if not isinstance(mp, dict):
            issues.append(Issue("sds.structure.mapping_type", 1, "FAIL", f"第 {idx + 1} 条映射不是对象", rule=1))
            continue
        missing = [k for k in SDS_REQUIRED_MAPPING_KEYS if k not in mp]
        if missing:
            issues.append(
                Issue(
                    "sds.structure.mapping_keys",
                    1,
                    "FAIL",
                    f"映射 #{idx + 1} ({mp.get('target_field', '?')}) 缺必填键: {missing}",
                    rule=1,
                )
            )
    if "needs_intermediate_table" in sds and not isinstance(sds.get("needs_intermediate_table"), bool):
        issues.append(Issue("sds.structure.intermediate_bool", 1, "FAIL", "needs_intermediate_table 必须显式为 bool", rule=1))


def _rule2_conclusion(sds: Dict[str, Any], issues: List[Issue]) -> None:
    for idx, mp in enumerate(sds.get("field_mappings", []) or []):
        conclusion = mp.get("conclusion")
        if conclusion not in ("accurate", "inaccurate", "undeterminable"):
            issues.append(
                Issue(
                    "sds.conclusion.missing",
                    1,
                    "FAIL",
                    f"映射 #{idx + 1} 缺结论（accurate/inaccurate/undeterminable）",
                    rule=2,
                )
            )
            continue
        if conclusion == "inaccurate" and not str(mp.get("correction", "")).strip():
            issues.append(
                Issue("sds.conclusion.no_correction", 1, "FAIL", f"映射 #{idx + 1} inaccurate 必须填 correction", rule=2)
            )
        if conclusion == "undeterminable":
            issues.append(
                Issue(
                    "sds.conclusion.undeterminable",
                    1,
                    "FAIL",
                    f"映射 #{idx + 1} undeterminable 不许进终版：应转 knowledge_gap 复核门",
                    rule=2,
                )
            )


def _rule3_traceability(sds: Dict[str, Any], issues: List[Issue], prefix: str = context.DEFAULT_KB_PREFIX) -> None:
    uri = str(sds.get("knowledge_source_uri", "")).strip()
    if not uri.startswith(prefix):
        issues.append(
            Issue(
                "sds.traceability.scope",
                1,
                "FAIL",
                f"knowledge_source_uri 必须以 {prefix} 开头（知识源铁律），当前: {uri!r}",
                rule=3,
            )
        )


def _rule4_layering(sds: Dict[str, Any], issues: List[Issue], modeling: Optional[Dict[str, Any]] = None) -> None:
    if str((modeling or {}).get("layer_policy", "enabled")) == "disabled":
        return
    target_layer = str(sds.get("target_table", {}).get("layer", "")).lower()
    for idx, mp in enumerate(sds.get("field_mappings", []) or []):
        src_layer = str(mp.get("source_layer", "")).lower()
        if target_layer in SUMMARY_LAYERS and src_layer in SUMMARY_LAYERS:
            if not sds.get("needs_intermediate_table"):
                issues.append(
                    Issue(
                        "sds.layering.summary_on_summary",
                        1,
                        "FAIL",
                        f"映射 #{idx + 1}: 目标({target_layer})是汇总层却直接消费下游汇总层({src_layer})，应判中间表",
                        rule=4,
                    )
                )


def _rule5_evidence_thresholds(sds: Dict[str, Any], limits: Dict[str, Any], issues: List[Issue]) -> None:
    sql = sds.get("sql_validation") or {}
    min_count = int(limits.get("sql_count_min", 2))
    sql_count = int(sql.get("sql_count", 0) or 0)
    if sql_count < min_count:
        issues.append(
            Issue(
                "sds.evidence.sql_count",
                1,
                "FAIL",
                f"SQL 验证数 {sql_count} < 下限 {min_count}",
                rule=5,
            )
        )
    pk_rate = float(limits.get("pk_match_rate", 0.995))
    dim_rate = float(limits.get("dim_match_rate", 0.95))
    for idx, mp in enumerate(sds.get("field_mappings", []) or []):
        ev = mp.get("sql_evidence") or {}
        rate = ev.get("join_match_rate")
        null_rate = ev.get("null_rate")
        kind = ev.get("key_kind") or mp.get("key_kind") or "dim"
        threshold = pk_rate if kind == "pk" else dim_rate
        problems = []
        if isinstance(rate, (int, float)) and rate < threshold:
            problems.append(f"join_match_rate={rate} < {threshold}")
        if kind == "pk" and isinstance(null_rate, (int, float)) and null_rate != 0:
            problems.append(f"主键 null_rate={null_rate} 必须为 0")
        if problems:
            explanation = str(mp.get("low_threshold_explanation", "")).strip()
            if explanation:
                issues.append(
                    Issue(
                        "sds.evidence.below_threshold_explained",
                        1,
                        "WARN",
                        f"映射 #{idx + 1} 低于阈值但已附解释: {'; '.join(problems)}",
                        rule=5,
                    )
                )
            else:
                issues.append(
                    Issue(
                        "sds.evidence.below_threshold",
                        1,
                        "FAIL",
                        f"映射 #{idx + 1} 低于阈值且无解释: {'; '.join(problems)}",
                        rule=5,
                    )
                )


def _rule6_evidence_compare(req_dir: Path, sds: Dict[str, Any], issues: List[Issue]) -> None:
    """evidence.json declared values must match sds_design.json exactly (abs_tol=1e-9)."""
    verification_dir = req_dir / "verification"
    evidence_path = verification_dir / "evidence.json"
    evidence = context.load_json(evidence_path)
    if evidence is None:
        issues.append(
            Issue(
                "sds.evidence.compare.missing",
                1,
                "HALTED",
                "verification/evidence.json 缺失：证据比对叫停，人工核查",
                rule=6,
                file="verification/evidence.json",
            )
        )
        return
    sql = sds.get("sql_validation") or {}
    compared = ["sql_count", "join_match_rate", "null_rate", "row_count"]
    mismatches = []
    for key in compared:
        if key in sql and key in evidence:
            a, b = sql[key], evidence[key]
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not math.isclose(float(a), float(b), abs_tol=1e-9):
                mismatches.append(f"{key}: sds={a} evidence={b}")
    declared_sql_files = evidence.get("sql_files") if isinstance(evidence.get("sql_files"), list) else []
    sql_count = int(sql.get("sql_count", 0) or 0)
    existing_sql = [p.name for p in verification_dir.glob("*.sql")]
    if len(existing_sql) < sql_count:
        mismatches.append(f".sql 文件数 {len(existing_sql)} < sql_count {sql_count}")
    if mismatches:
        issues.append(
            Issue(
                "sds.evidence.compare.mismatch",
                1,
                "HALTED",
                "evidence.json 与 sds_design.json 不一致，叫停人工核查: " + "; ".join(mismatches[:8]),
                rule=6,
                file="verification/evidence.json",
            )
        )


def _rule7_knowledge_gaps(sds: Dict[str, Any], issues: List[Issue], prefix: str = context.DEFAULT_KB_PREFIX) -> None:
    gaps = sds.get("knowledge_gaps") or []
    if not gaps:
        return
    traces = sds.get("knowledge_gap_evidence") or []
    if not isinstance(traces, list) or len(traces) < 3:
        issues.append(
            Issue(
                "sds.knowledge_gap.traces",
                1,
                "FAIL",
                "knowledge_gaps 非空但检索轨迹 <3 条，不许进终版",
                rule=7,
            )
        )
        return
    hit_root = any(bool(t.get("hit_root")) for t in traces if isinstance(t, dict))
    if not hit_root:
        issues.append(
            Issue(
                "sds.knowledge_gap.no_root_hit",
                1,
                "FAIL",
                f"knowledge_gaps 非空且无任何轨迹命中知识库根（{prefix}），不许进终版",
                rule=7,
            )
        )


def _rule8_backfill(sds: Dict[str, Any], issues: List[Issue]) -> None:
    mode = sds.get("mode")
    declared = str(sds.get("declared_source", "")).strip()
    has_inaccurate = any(
        (m or {}).get("conclusion") == "inaccurate" for m in sds.get("field_mappings", []) or []
    )
    backfill_required = (mode == "generate" and bool(declared)) or (mode == "verify" and has_inaccurate)
    backfill = sds.get("knowledge_backfill") or {}
    if backfill_required:
        resolved = backfill.get("resolved")
        if resolved not in ("synced", "declined"):
            issues.append(
                Issue(
                    "sds.backfill.pending",
                    1,
                    "FAIL",
                    "命中增量沉淀候选：knowledge_backfill.resolved 必须为 synced 或 declined",
                    rule=8,
                )
            )
    elif sds.get("confirmed"):
        # 不命中候选时允许无该对象
        pass


def validate_sds_design(
    req_dir: Path,
    limits: Optional[Dict[str, Any]] = None,
    require_confirmed: bool = True,
    kb_prefix: Optional[str] = None,
    modeling: Optional[Dict[str, Any]] = None,
) -> List[Issue]:
    issues: List[Issue] = []
    sds_path = req_dir / "sds_design.json"
    sds = context.load_json(sds_path)
    if not isinstance(sds, dict):
        issues.append(Issue("sds.file.missing", 1, "FAIL", "sds_design.json 缺失", file="sds_design.json"))
        return issues
    limits = limits or {}
    prefix = kb_prefix or context.DEFAULT_KB_PREFIX
    _rule1_structure(sds, issues)
    _rule2_conclusion(sds, issues)
    _rule3_traceability(sds, issues, prefix)
    _rule4_layering(sds, issues, modeling)
    _rule5_evidence_thresholds(sds, limits, issues)
    _rule6_evidence_compare(req_dir, sds, issues)
    _rule7_knowledge_gaps(sds, issues, prefix)
    _rule8_backfill(sds, issues)
    if require_confirmed and sds.get("confirmed") is not True:
        issues.append(Issue("sds.confirm.pending", 1, "FAIL", "confirmed != true：确认门未通过", rule=1))
    return issues


# ======================================================================
# 2. SQL compliance checker (17 checks, 9 zero-tolerance)
# ======================================================================
def _extract_annotations(text: str) -> Dict[str, str]:
    """SQL 头部注解：任意 `-- key: value`，key 允许连字符（schedule-timezone 之类），
    value 取整行剩余内容（cron 表达式本身就是多段，截到第一个空格会丢信息）。

    非 ASCII 开头的注释（中文说明）不会被当成注解；重复键取首次出现（与改动前一致）。
    """
    out: Dict[str, str] = {}
    for match in re.finditer(
        r"^\s*--\s*([A-Za-z][A-Za-z0-9-]*)\s*[:=]\s*(.+?)\s*$", text, re.MULTILINE
    ):
        out.setdefault(match.group(1).strip().lower(), match.group(2).strip())
    return out


def _first_target_table(text: str) -> Optional[str]:
    m = re.search(
        # 真实联调踩到：`CREATE TABLE IF NOT EXISTS x` 会被旧正则把 "IF" 当成目标表，
        # 于是所有 IF NOT EXISTS 的建表都报「文件名与目标表不一致」。
        # 另外 `INSERT OVERWRITE x`（部分引擎不写 TABLE）也要认。
        r"(?:CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"|INSERT\s+(?:OVERWRITE|INTO)\s+(?:TABLE\s+)?)"
        r"([`\"\[\]]?[\w$.-]+[`\"\[\]]?)",
        text,
        re.IGNORECASE,
    )
    return m.group(1).strip().strip("`\"[]") if m else None


def sql_compliance_check(
    text: str,
    *,
    filename: str = "",
    is_dp_task: bool = False,
    ddl_targets: Optional[Sequence[str]] = None,
    dialect: Optional[str] = None,
    sql_format: Optional[str] = None,
    scheduler: Optional[str] = None,
) -> List[Issue]:
    """17 rules. Rules 1..9 are zero-tolerance; 10..17 are WARN quality checks.

    ``dialect``（可选）追加按引擎的规则：同一条 SQL 在 Hive 与 ClickHouse 上成立与否不同。
    不传 dialect 时行为与改动前完全一致——基线 17 项不依赖任何引擎。
    """
    issues: List[Issue] = []
    upper = text.upper()
    annotations = _extract_annotations(text)
    declared_table = annotations.get("table")
    target = declared_table or _first_target_table(text)
    phase = 3
    # 只读 SQL（SELECT/WITH/EXPLAIN 等）没有"写入目标"，不该被强制要求 -- table 注解，
    # 也不该拿文件名去比对目标表——P4 用例文件天然叫 tc_0001.sql，旧规则会把它误判成 FAIL。
    read_only = not re.search(
        r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE|REPLACE|LOAD|COPY|EXPORT)\b",
        upper,
    )

    # --- hard rules 1-9 -------------------------------------------------
    if not target:
        if read_only:
            issues.append(
                Issue(
                    "sql.compliance.readonly",
                    phase,
                    "WARN",
                    "只读 SQL：不需要 -- table 目标表注解（用例/校验查询）",
                    rule=1,
                    file=filename,
                )
            )
        else:
            issues.append(
                Issue("sql.compliance.target", phase, "FAIL", "无法从 SQL 或 -- table: 注解中识别目标表", rule=1, file=filename)
            )
    elif (
        not read_only
        and filename
        and target.split(".")[-1] not in Path(filename).stem
        and Path(filename).stem not in target
    ):
        issues.append(
            Issue(
                "sql.compliance.name_mismatch",
                phase,
                "FAIL",
                f"文件名 {Path(filename).stem} 与目标表 {target} 不一致",
                rule=1,
                file=filename,
            )
        )

    if is_dp_task and re.search(r"\bALTER\s+TABLE\b", upper):
        issues.append(
            Issue(
                "sql.compliance.alter_in_task",
                phase,
                "FAIL",
                "周期任务(dp_task)禁止 ALTER TABLE：DDL 必须留在 ddl/ 目录",
                rule=2,
                file=filename,
            )
        )

    for keyword in ("DELETE", "UPDATE"):
        if re.search(rf"\b{keyword}\b", upper) and not re.search(rf"\b{keyword}\b[^;]*\bWHERE\b", upper):
            issues.append(
                Issue(
                    "sql.compliance.full_scan_write",
                    phase,
                    "FAIL",
                    f"{keyword} 无 WHERE，禁止全表写操作",
                    rule=3,
                    file=filename,
                )
            )

    if re.search(r"\bprod\b", upper, re.IGNORECASE) and annotations.get("env", "dev") == "dev":
        issues.append(
            Issue(
                "sql.compliance.prod_hardcode",
                phase,
                "FAIL",
                "疑似硬编码生产库（含 prod 标识且未标注 -- env: prod）",
                rule=4,
                file=filename,
            )
        )

    # coarse syntax sanity: balanced brackets/quotes, no stray backticks
    if upper.count("(") != upper.count(")") or upper.count("[") != upper.count("]"):
        issues.append(Issue("sql.compliance.unbalanced", phase, "FAIL", "括号不配对，语法护栏拦截", rule=5, file=filename))
    if upper.count("'") % 2 == 1:
        issues.append(Issue("sql.compliance.unbalanced_quote", phase, "FAIL", "单引号不配对，语法护栏拦截", rule=5, file=filename))

    ddl_targets = ddl_targets or []
    if ddl_targets and "CREATE TABLE" not in upper:
        inferred = target.split(".")[-1] if target else ""
        if inferred and inferred not in ddl_targets:
            issues.append(
                Issue(
                    "sql.compliance.ddl_missing",
                    phase,
                    "FAIL",
                    f"写入 {inferred} 但 ddl/ 下无对应 CREATE TABLE",
                    rule=6,
                    file=filename,
                )
            )

    if re.search(r"\bINSERT\b", upper) and re.search(r"\bSELECT\s*\*", upper, re.IGNORECASE):
        issues.append(
            Issue(
                "sql.compliance.select_star",
                phase,
                "FAIL",
                "INSERT ... SELECT 中禁止 SELECT *：必须显式列清单",
                rule=7,
                file=filename,
            )
        )

    if is_dp_task:
        issues.extend(_schedule_issues(annotations, phase, filename, scheduler))

    # 第 9 条只针对"搬数据"的语句：INSERT 与 CTAS（CREATE TABLE ... AS SELECT）要说清上游。
    # 纯建表（列定义/ALTER/分区）没有上游概念——旧写法把 `CREATE TABLE` 也纳入，
    # 结果所有新建表 DDL 都被判 FAIL。真实引擎联调时正是它把建表挡在门外
    # （见 docs/TEST-REPORT-postgres-live.md 的失败案例一）。
    if re.search(r"\bINSERT\b", upper) or re.search(r"\bCREATE\s+TABLE\b[\s\S]{0,200}?\bAS\s+SELECT\b", upper):
        if not annotations.get("source") and not re.search(r"\bFROM\b", upper):
            issues.append(
                Issue(
                    "sql.compliance.no_lineage",
                    phase,
                    "FAIL",
                    "缺 -- source: 上游血缘注解，无法审计",
                    rule=9,
                    file=filename,
                )
            )

    # --- soft rules 10-17 ------------------------------------------------
    if re.search(r"\bfrom\b", upper) and not re.search(r"\bwhere\b", upper):
        issues.append(Issue("sql.compliance.no_filter_warn", phase, "WARN", "无 WHERE 过滤（大表扫描风险）", rule=10, file=filename))
    lines = text.splitlines()
    long_lines = [i + 1 for i, line in enumerate(lines) if len(line) > 180]
    if long_lines:
        issues.append(Issue("sql.compliance.long_line", phase, "WARN", f"超长行: {long_lines[:5]}", rule=11, file=filename))
    if len(lines) > 3 and not text.rstrip().endswith(";"):
        issues.append(Issue("sql.compliance.no_semicolon", phase, "WARN", "语句未以分号结尾", rule=12, file=filename))
    nested = len(re.findall(r"\bSELECT\b", upper, re.IGNORECASE))
    if nested >= 4 and "WITH " not in upper and "WITH" not in upper:
        issues.append(Issue("sql.compliance.no_cte", phase, "WARN", "多层子查询建议改写为 CTE", rule=13, file=filename))
    if re.search(r"\bgroup\s+by\b", upper) and not re.search(r"\b(?:count|sum|avg|min|max)\b", upper, re.IGNORECASE):
        issues.append(Issue("sql.compliance.group_no_agg", phase, "WARN", "GROUP BY 无聚合函数？请核对语义", rule=14, file=filename))
    if not annotations.get("table") and target:
        issues.append(Issue("sql.compliance.no_table_annotation", phase, "WARN", "建议加 -- table: 注解", rule=15, file=filename))
    if re.search(r"\bcast\s*\(", upper, re.IGNORECASE) and re.search(r"\bdate\b", upper, re.IGNORECASE) is None and "ds" not in upper.lower():
        issues.append(Issue("sql.compliance.partition_hint", phase, "WARN", "未发现分区字段（ds/date）相关写法，请核对", rule=16, file=filename))
    if text.strip().lower().startswith(("select", "insert", "update", "delete")) and ";" in text and text.count(";") > 1:
        issues.append(Issue("sql.compliance.multi_statement", phase, "WARN", "单文件多语句：请拆文件或用 batch 标注", rule=17, file=filename))

    # --- dialect rules (仅当显式给出 dialect 时生效) ----------------------
    for hit in dialects.dialect_issues(text, dialect, filename):
        issues.append(
            Issue(
                str(hit["id"]),
                phase,
                str(hit["severity"]),
                str(hit["message"]),
                rule=int(hit["rule"]) or None,
                file=filename,
                detail={"dialect": hit["dialect"], "repair": hit["repair"]},
            )
        )

    # --- lakehouse format rules (仅当显式给出 format 时生效) --------------
    for hit in formats.format_issues(text, sql_format or "") if sql_format else []:
        issues.append(
            Issue(
                str(hit["id"]),
                phase,
                str(hit["severity"]),
                str(hit["message"]),
                file=filename,
                detail={"format": hit["format"], "repair": hit["repair"]},
            )
        )
    return issues


def project_engine_settings(req_dir: Path) -> Dict[str, Optional[str]]:
    """按项目配置推导方言与湖表格式（供 P3 出口门用）。

    推导不出来就返回 None（只用基线 17 项）。未登记 kind/format 的报错责任在
    `platform doctor` / `make_platform` / `ddl render`，不在这里制造第二个错误面。
    """
    settings: Dict[str, Optional[str]] = {"dialect": None, "format": None, "scheduler": None}
    node = Path(req_dir)
    for _ in range(4):
        node = node.parent
        if (node / ".data-dev" / "config.json").is_file():
            config = context.load_config(node)
            platform = config.get("platform") or {}
            kind = str(platform.get("kind") or "cli")
            fmt = str(platform.get("format") or "none").strip().lower()
            if fmt in ("", "none"):
                fmt = ""
            settings["format"] = fmt or None
            settings["scheduler"] = str((config.get("scheduler") or {}).get("kind") or "cron").strip().lower()
            try:
                # 用 dialect_from_config 而不是 dialect_of(kind)：kind=command 时方言来自
                # platform.command.engine（真实引擎联调发现只按 kind 会退化成 generic）
                settings["dialect"] = engines.dialect_from_config(config)
            except engines.UnknownEngine:
                settings["dialect"] = None
            return settings
    return settings


def project_dialect(req_dir: Path) -> Optional[str]:
    """兼容旧调用：只要方言。"""
    return project_engine_settings(req_dir)["dialect"]


def scan_sql_tree(
    req_dir: Path,
    dialect: Optional[str] = None,
    sql_format: Optional[str] = None,
    scheduler: Optional[str] = None,
) -> List[Issue]:
    """Run the 17-rule compliance scan over ddl/ etl/ dp_task/（外加该引擎的方言规则）。"""
    issues: List[Issue] = []
    if dialect is None:
        dialect = project_dialect(req_dir)
    if sql_format is None:
        sql_format = project_engine_settings(req_dir)["format"]
    if scheduler is None:
        scheduler = project_engine_settings(req_dir)["scheduler"]
    ddl_targets = [p.stem for p in (req_dir / "ddl").glob("*.sql")] if (req_dir / "ddl").is_dir() else []
    for sub, is_task in (("ddl", False), ("etl", False), ("dp_task", True)):
        base = req_dir / sub
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.sql")):
            text = context.read_text(path)
            issues.extend(
                sql_compliance_check(
                    text,
                    filename=path.name,
                    is_dp_task=is_task,
                    ddl_targets=ddl_targets if sub != "ddl" else None,
                    dialect=dialect,
                    sql_format=sql_format,
                    scheduler=scheduler,
                )
            )
    return issues


# ======================================================================
# 3. Phase exit gates P2..P6 + submit quality
# ======================================================================
ARCH_TABLES_KEY = "tables"


def declared_tables(req_dir: Path) -> Tuple[Optional[set], str]:
    """从 ``architecture_doc.md`` 的 ```json 机读块取「交付表清单」。

    返回 ``(表名集合, 原因)``：集合为 None 表示拿不到，原因指明断在哪一步。
    **刻意只认 ``tables[].name``**——action/layer 允许写但不校验，避免一次改太多
    （先把"造的表 == 设计声明的表"这条钉死）。
    """
    doc = context.read_text(req_dir / "architecture_doc.md")
    block = context.extract_json_block(doc)
    if block is None:
        return None, "architecture_doc.md 里找不到 ```json 机读块（或不是合法 JSON）"
    if not isinstance(block, dict):
        return None, "机读块必须是 JSON 对象"
    rows = block.get(ARCH_TABLES_KEY)
    if not isinstance(rows, list) or not rows:
        return None, f"机读块缺非空 {ARCH_TABLES_KEY} 数组"
    names: List[str] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            return None, f"{ARCH_TABLES_KEY}[{idx}] 不是对象"
        name = str(row.get("name") or "").strip()
        if not name:
            return None, f"{ARCH_TABLES_KEY}[{idx}] 缺 name"
        if name in names:
            return None, f"{ARCH_TABLES_KEY} 里重复声明: {name}"
        names.append(name)
    return set(names), ""


def validate_phase2_architecture(req_dir: Path, level: Optional[str]) -> List[Issue]:
    issues: List[Issue] = []
    spec = req_dir / "requirement_spec.md"
    if not _nonempty_markdown(spec):
        issues.append(Issue("arch.spec.missing", 2, "FAIL", "requirement_spec.md 缺失或为空", file="requirement_spec.md"))
    doc_path = req_dir / "architecture_doc.md"
    doc = context.read_text(doc_path)
    if len(doc.strip()) < 10:
        issues.append(Issue("arch.doc.missing", 2, "FAIL", "architecture_doc.md 缺失或为空", file="architecture_doc.md"))
    else:
        level_hit = re.search(r"\b(L[0-3])\b", doc)
        if not level_hit:
            issues.append(Issue("arch.level.missing", 2, "FAIL", "architecture_doc.md 未标注难度级别（L1/L2/L3）", file="architecture_doc.md"))
        elif level and level_hit.group(1) != level:
            issues.append(
                Issue("arch.level.mismatch", 2, "FAIL", f"架构难度 {level_hit.group(1)} 与状态机 level={level} 不一致", file="architecture_doc.md")
            )
        if level in ("L2", "L3") and "mermaid" not in doc.lower() and not re.search(r"!\[[^]]*\]\([^)]+\)", doc):
            issues.append(Issue("arch.link.missing", 2, "FAIL", "L2/L3 必须含数据链路图（mermaid 或图片）", file="architecture_doc.md"))
        # 交付表清单：P3 会拿它和 ddl/etl/dp_task 的实际表名做等值比对（PIPE-002）
        tables, reason = declared_tables(req_dir)
        if tables is None:
            issues.append(
                Issue(
                    "arch.tables.missing",
                    2,
                    "FAIL",
                    f"交付表清单不可用: {reason}。架构必须给出 ```json 机读块 {{\"tables\":[{{\"name\":\"<表名>\"}}]}}",
                    file="architecture_doc.md",
                )
            )
    html_path = req_dir / "pipeline.html"
    html = context.read_text(html_path)
    if level in ("L2", "L3") and len(html.strip()) < 20:
        issues.append(Issue("arch.html.missing", 2, "FAIL", "L2/L3 缺 pipeline.html", file="pipeline.html"))
    for path, name in ((doc_path, "architecture_doc.md"), (html_path, "pipeline.html")):
        text = context.read_text(path)
        if re.search(r"\b(TODO|FIXME|placeholder|lorem ipsum)\b", text, re.IGNORECASE):
            issues.append(Issue("arch.placeholder", 2, "FAIL", f"{name} 残留占位符", file=name))
    return issues


def validate_phase3_data_dev(req_dir: Path, state: Dict[str, Any]) -> List[Issue]:
    issues: List[Issue] = []
    level = state.get("level")
    ctx = state.get("context") or {}
    limits = state.get("limits") or {}
    empty_dirs = []
    table_sets: Dict[str, set] = {}
    for sub in ("ddl", "etl", "dp_task"):
        base = req_dir / sub
        if not base.is_dir():
            empty_dirs.append(sub)
            continue
        files = [p.stem for p in base.glob("*.sql")]
        if not files:
            empty_dirs.append(sub)
        table_sets[sub] = set(files)
    if empty_dirs:
        issues.append(Issue("dev.empty_dir", 3, "FAIL", f"空目录/无 SQL: {empty_dirs}"))
    else:
        consistent = table_sets["ddl"] == table_sets["etl"] == table_sets["dp_task"]
        if not consistent:
            only_ddl = table_sets["ddl"] - table_sets["etl"] - table_sets["dp_task"]
            only_etl = table_sets["etl"] - table_sets["ddl"] - table_sets["dp_task"]
            only_task = table_sets["dp_task"] - table_sets["ddl"] - table_sets["etl"]
            issues.append(
                Issue(
                    "dev.naming_inconsistent",
                    3,
                    "FAIL",
                    f"三层目录表清单不一致（仅ddl:{sorted(only_ddl)[:4]} 仅etl:{sorted(only_etl)[:4]} 仅task:{sorted(only_task)[:4]}）",
                )
            )
    issues.extend(scan_sql_tree(req_dir))

    # 架构 → 研发的契约：造的表必须与 architecture_doc.md 声明的交付表清单**等值**。
    # 多造 = 设计了才该造；少造 = 砍表必须留下记录（回跳 P2 改设计）。
    built = table_sets.get("ddl") or set()
    declared, reason = declared_tables(req_dir)
    if declared is None:
        issues.append(Issue("dev.arch.tables_missing", 3, "FAIL", f"架构交付表清单不可用: {reason}"))
    elif built:
        not_built = sorted(declared - built)
        not_declared = sorted(built - declared)
        if not_built:
            issues.append(
                Issue(
                    "dev.arch.table_missing",
                    3,
                    "FAIL",
                    f"架构声明了但没造: {not_built[:5]}（要改设计请回跳 P2：advance --allow-back --reason）",
                    file="architecture_doc.md",
                )
            )
        if not_declared:
            issues.append(
                Issue(
                    "dev.arch.table_extra",
                    3,
                    "FAIL",
                    f"造了架构没声明的表: {not_declared[:5]}（先回 P2 补进交付表清单）",
                    file="architecture_doc.md",
                )
            )
    # 白捡一条：SDS 的目标表必须真的在交付表里（现有数据，零新增声明）
    sds = context.load_json(req_dir / "sds_design.json")
    if isinstance(sds, dict) and built:
        sds_target = str((sds.get("target_table") or {}).get("name") or "").strip()
        if sds_target and sds_target not in built:
            issues.append(
                Issue(
                    "dev.sds_target_not_built",
                    3,
                    "FAIL",
                    f"SDS 目标表 {sds_target} 不在交付表里: {sorted(built)[:5]}",
                    file="sds_design.json",
                )
            )

    exec_path = req_dir / "etl_dev_exec.json"
    exec_ev = context.load_json(exec_path)
    ok_ev, reason = execution_evidence_ok(exec_ev, limits)
    issues.extend(evidence_level_issues(exec_ev, 3, "etl_dev_exec.json"))
    if not ok_ev:
        issues.append(
            Issue(
                "dev.exec_evidence.missing",
                3,
                "FAIL",
                f"开发环境真跑证据不合格: {reason}",
                file="etl_dev_exec.json",
            )
        )
    else:
        ledger_ids = execution_ledger_ids(req_dir)
        if ledger_ids is None and (state.get("mode") or "mock") == "live":
            issues.append(
                Issue(
                    "dev.exec_evidence.no_ledger_store",
                    3,
                    "FAIL",
                    "live 模式要求执行账本存在：请用 engine platform run-sql 真跑并生成 .data-dev/runtime/executions.json",
                    file="etl_dev_exec.json",
                )
            )
        elif ledger_ids is not None and str(exec_ev.get("query_id")) not in ledger_ids:
            issues.append(
                Issue(
                    "dev.exec_evidence.no_ledger",
                    3,
                    "FAIL",
                    "etl_dev_exec.json 的 query_id 不在引擎执行账本中（证据必须来自 engine platform run-sql）",
                    file="etl_dev_exec.json",
                )
            )
        elif ledger_ids is not None:
            evidence_binds_to_current_sql(req_dir, exec_ev, 3, issues)

    if ctx.get("self_check_result") != "passed":
        issues.append(Issue("dev.self_check.pending", 3, "FAIL", "self_check 未通过：context.self_check_result != passed"))
    if ctx.get("verify_result") != "passed":
        issues.append(Issue("dev.verify.pending", 3, "FAIL", "离线引擎重跑验证未过：context.verify_result != passed"))

    code_diff_required = ctx.get("code_diff.required") is True
    if code_diff_required and ctx.get("code_diff_done") is not True:
        issues.append(
            Issue("dev.code_diff.pending", 3, "FAIL", "改现有任务必须 code_diff 比对线上 PUBLISHED 基准并展示，code_diff_done != true")
        )
    if not code_diff_required:
        issues.append(Issue("dev.code_diff.new_task", 3, "WARN", "无线上基准（新表场景），跳过 code_diff 强校验"))
    return issues


def _parse_test_report_markers(report_text: str) -> Dict[str, str]:
    """Return {test_id: status} for lines mentioning TC-xxxx and a pass/fail marker."""
    out: Dict[str, str] = {}
    for line in report_text.splitlines():
        m = re.search(r"\b(TC-\d+)\b", line)
        if not m:
            continue
        if any(mark in line for mark in ("✅", "PASS", "通过", "SUCCESS", "✔")):
            out[m.group(1)] = "SUCCESS"
        elif any(mark in line for mark in ("❌", "FAIL", "失败", "FAILED")):
            out[m.group(1)] = "FAILED"
    return out


def validate_phase4_testing(req_dir: Path, state: Dict[str, Any]) -> List[Issue]:
    issues: List[Issue] = []
    level = state.get("level") or "L1"
    limits = state.get("limits") or {}
    report = context.read_text(req_dir / "test_report.md")
    if len(report.strip()) < 10:
        issues.append(Issue("test.report.missing", 4, "FAIL", "test_report.md 缺失或为空", file="test_report.md"))
    runs_path = req_dir / "test_runs.json"
    runs = context.load_json(runs_path)
    if not isinstance(runs, dict):
        issues.append(
            Issue(
                "test.runs.missing",
                4,
                "FAIL",
                "缺 test_runs.json 机器证据：报告标✅的 TC 必须能在其中找到同 test_id 的 SUCCESS + query_id",
                file="test_runs.json",
            )
        )
        return issues
    run_list = runs.get("runs") if isinstance(runs.get("runs"), list) else []
    by_id = {str(r.get("test_id", "")): r for r in run_list if isinstance(r, dict)}
    for run in run_list:
        if isinstance(run, dict) and run.get("status") == "FAILED":
            issues.append(
                Issue("test.case.failed", 4, "FAIL", f"用例 {run.get('test_id')} FAILED", file="test_runs.json")
            )
    ledger_ids = execution_ledger_ids(req_dir)
    if ledger_ids is None and (state.get("mode") or "mock") == "live":
        issues.append(
            Issue(
                "test.evidence.no_ledger_store",
                4,
                "FAIL",
                "live 模式要求测试经引擎执行生成账本（.data-dev/runtime/executions.json 不存在）",
                file="test_runs.json",
            )
        )
    elif ledger_ids is not None:
        for run in run_list:
            if isinstance(run, dict) and run.get("status") == "SUCCESS" and str(run.get("query_id", "")).strip():
                if str(run.get("query_id")) not in ledger_ids:
                    issues.append(
                        Issue(
                            "test.evidence.no_ledger",
                            4,
                            "FAIL",
                            f"{run.get('test_id')} 的 query_id 不在引擎执行账本中（禁止手写执行证据）",
                            file="test_runs.json",
                        )
                    )
    reported = _parse_test_report_markers(report)
    for test_id, marker in reported.items():
        run = by_id.get(test_id)
        if marker == "FAILED":
            continue
        if not run:
            issues.append(
                Issue(
                    "test.evidence.no_run",
                    4,
                    "FAIL",
                    f"报告标✅的 {test_id} 在 test_runs.json 无对应记录（禁止只写报告不实跑）",
                    file="test_report.md",
                )
            )
            continue
        if run.get("status") != "SUCCESS" or not str(run.get("query_id", "")).strip():
            issues.append(
                Issue(
                    "test.evidence.no_query_id",
                    4,
                    "FAIL",
                    f"{test_id} 在 test_runs.json 缺 status=SUCCESS + 非空 query_id",
                    file="test_runs.json",
                )
            )
    # 前置证据与 P3 走同一套判定：status + 非空 query_id + env + 时效 + 账本命中。
    # 只看 status 的话，一份 24 小时有效期的证据会在 P3 之后变成永久有效。
    exec_ev = context.load_json(req_dir / "etl_dev_exec.json") or {}
    ok_ev, reason = execution_evidence_ok(exec_ev, limits)
    if not ok_ev:
        issues.append(
            Issue("test.precondition.etl", 4, "FAIL", f"前置 etl_dev_run 证据不合格: {reason}")
        )
    else:
        ledger_ids = execution_ledger_ids(req_dir)
        if ledger_ids is not None and str(exec_ev.get("query_id")) not in ledger_ids:
            issues.append(
                Issue(
                    "test.precondition.etl_ledger",
                    4,
                    "FAIL",
                    "前置 etl_dev_exec.json 的 query_id 不在引擎执行账本中",
                    file="etl_dev_exec.json",
                )
            )
        # 与 P3 同一道绑定：过 P3 之后改 ETL，不能带着旧证据一路走到 P4。
        evidence_binds_to_current_sql(req_dir, exec_ev, 4, issues)
    # count floor is a WARN (doc: 下限只是 WARN)
    min_count = int((limits.get("test_count_min") or {}).get(level, 1))
    if len(reported) < min_count:
        issues.append(
            Issue(
                "test.coverage.low",
                4,
                "WARN",
                f"报告用例数 {len(reported)} < {level} 下限 {min_count}（WARN，非硬阻断）",
            )
        )
    return issues


def validate_phase5_deploy(req_dir: Path, state: Dict[str, Any]) -> List[Issue]:
    issues: List[Issue] = []
    summary = context.read_text(req_dir / "delivery_summary.md")
    if len(summary.strip()) < 10:
        issues.append(Issue("deploy.summary.missing", 5, "FAIL", "delivery_summary.md 缺失或少于10行", file="delivery_summary.md"))
    elif len(summary.splitlines()) < 10:
        issues.append(Issue("deploy.summary.short", 5, "FAIL", "delivery_summary.md 少于10行", file="delivery_summary.md"))
    log_path = req_dir / "deploy_log.json"
    if log_path.exists():
        data = context.load_json(log_path)
        if not isinstance(data, dict):
            issues.append(Issue("deploy.log.invalid", 5, "FAIL", "deploy_log.json 非法 JSON", file="deploy_log.json"))
    else:
        issues.append(Issue("deploy.log.missing", 5, "WARN", "deploy_log.json 缺失（WARN：部署记录建议保留）"))
    plan = context.load_json(req_dir / "deploy_plan.json") or {}
    plan_env = str(plan.get("env") or "").lower()
    is_prod = plan_env in ("prod", "production")
    deploy_cfg = state.get("deploy") or {}
    if is_prod and deploy_cfg.get("require_rollback"):
        missing_rollback = [
            str(i.get("table"))
            for i in (plan.get("items") or [])
            if i.get("action") in ("create", "alter") and not str(i.get("rollback_sql_file", "")).strip()
        ]
        if missing_rollback:
            issues.append(
                Issue(
                    "deploy.rollback.missing",
                    5,
                    "FAIL",
                    f"生产部署要求回滚预案（deploy.require_rollback=true），缺 rollback_sql_file: {missing_rollback[:5]}",
                )
            )
    if is_prod and plan.get("publish_confirmed") is not True:
        issues.append(Issue("deploy.plan.unconfirmed", 5, "WARN", "deploy_plan.publish_confirmed != true（WARN：真实约束靠 publish_gate 的 [已确认发布] token）"))
    ctx = state.get("context") or {}
    if is_prod and ctx.get("tracker_transitioned") != "待验收":
        issues.append(Issue("deploy.tracker.not_accepted", 5, "WARN", "生产工单未确认流转到待验收（WARN）"))
    # 发布/回刷的必须是**验证过的那份代码**：过 P4 之后又改 ETL，不该还能部署。
    # 这里只查绑定、不重复查时效——时效说的是"dev 什么时候跑过"，P4 已经判过；
    # 而"部署的是不是被测过的那一份"在 P5 必须仍然成立。
    # 注：P5 阶段不允许再改 etl_dev_exec.json（gates.PHASE_FILE_MAP 限定 3/4 阶段可写，
    # runtime_guard 拦截 shell 改写），所以这里直接读现成证据即可。
    deploy_exec_ev = context.load_json(req_dir / "etl_dev_exec.json")
    if isinstance(deploy_exec_ev, dict) and str(deploy_exec_ev.get("query_id") or "").strip():
        evidence_binds_to_current_sql(req_dir, deploy_exec_ev, 5, issues)
    # ---- 后半程门：数据回刷 / 数据质量必须留下可核验产物 ----
    plan_items = [i for i in (plan.get("items") or []) if isinstance(i, dict)]
    backfill_items = [i for i in plan_items if i.get("action") == "backfill"]
    if backfill_items:
        runs = _ledger_runs(req_dir, "backfill_runs.json")
        if runs is None:
            if str(state.get("mode") or "mock").lower() == "live":
                # live + kind=cli：回刷由平台侧外部 CLI 执行，引擎拿不到逐日记录
                issues.append(
                    Issue(
                        "deploy.backfill.no_ledger",
                        5,
                        "WARN",
                        "缺 backfill_runs 账本：当前适配器未在引擎内记录回刷（kind=cli 外部 CLI 由平台侧负责），本项无法机器核验",
                    )
                )
            else:
                issues.append(
                    Issue(
                        "deploy.backfill.no_ledger",
                        5,
                        "FAIL",
                        "mock/引擎内执行模式下缺 backfill_runs 账本：回刷没有留下任何执行证据",
                    )
                )
        else:
            done = {str(r.get("key")) for r in runs if r.get("status") == "SUCCESS"}
            missing_backfill = [
                str(i.get("table")) + "|" + str(i.get("range_start")) + "|" + str(i.get("range_end"))
                for i in backfill_items
                if str(i.get("table")) + "|" + str(i.get("range_start")) + "|" + str(i.get("range_end"))
                not in done
            ]
            if missing_backfill:
                issues.append(
                    Issue(
                        "deploy.backfill.missing",
                        5,
                        "FAIL",
                        f"deploy_plan 声明的回刷项在 backfill_runs 账本里没有成功记录: {missing_backfill[:5]}",
                    )
                )
    dqc_items = [i for i in plan_items if i.get("action") == "dqc_publish"]
    if dqc_items:
        dqc_data = context.load_json(req_dir / "dqc_results.json")
        raw_rows = dqc_data.get("results") if isinstance(dqc_data, dict) else None
        rows = [r for r in raw_rows if isinstance(r, dict)] if isinstance(raw_rows, list) else []
        deployed = {str(r.get("table")) for r in rows if r.get("deployed") is True}
        missing_dqc = [str(i.get("table")) for i in dqc_items if str(i.get("table")) not in deployed]
        if missing_dqc:
            issues.append(
                Issue(
                    "deploy.dqc.not_deployed",
                    5,
                    "FAIL",
                    f"deploy_plan 声明的 DQC 布防项缺 deployed=true 记录: {missing_dqc[:5]}（写入 dqc_results.json）",
                    file="dqc_results.json",
                )
            )
    return issues


def validate_phase6_semantic(req_dir: Path, state: Dict[str, Any], semantic_enabled: bool = True) -> List[Issue]:
    if not semantic_enabled:
        return []
    issues: List[Issue] = []
    summary = context.read_text(req_dir / "semantic_update_summary.md")
    if len(summary.strip()) < 10:
        issues.append(
            Issue("semantic.summary.missing", 6, "FAIL", "semantic_update_summary.md 缺失或为空", file="semantic_update_summary.md")
        )
    elif len(summary.splitlines()) < 10:
        issues.append(Issue("semantic.summary.short", 6, "FAIL", "semantic_update_summary.md 少于10行", file="semantic_update_summary.md"))
    elif not re.search(r"\b(model|模型|yaml|视图|指标|维度)\b", summary, re.IGNORECASE):
        issues.append(Issue("semantic.model_info.missing", 6, "FAIL", "无模型信息：semantic 摘要必须说明更新的模型/YAML"))
    issues.append(Issue("semantic.kb_publish.record", 6, "WARN", "kb-publish 发布记录未本地核验（WARN：真实门在 knowledge-base 插件内）"))
    issues.append(Issue("semantic.dpsl.validate", 6, "WARN", "dpsl.py 校验结果未留痕（WARN）"))
    issues.append(Issue("semantic.ov.uri", 6, "WARN", "OV URI 未留痕（WARN）"))
    return issues


def stage_gate(phase: int, req_dir: Path, state: Optional[Dict[str, Any]] = None, kb_prefix: Optional[str] = None) -> List[Issue]:
    """Exit gate for ``phase`` (the phase being validated)."""
    state = state or (context.load_json(context.state_path(req_dir)) or {})
    limits = state.get("limits") or {}
    modeling = state.get("modeling") or {}
    semantic_enabled = bool((state.get("semantic") or {}).get("enabled", True))
    if phase == 1:
        return validate_sds_design(req_dir, limits=limits, kb_prefix=kb_prefix, modeling=modeling)
    if phase == 2:
        return validate_phase2_architecture(req_dir, state.get("level"))
    if phase == 3:
        return validate_phase3_data_dev(req_dir, state)
    if phase == 4:
        return validate_phase4_testing(req_dir, state)
    if phase == 5:
        return validate_phase5_deploy(req_dir, state)
    if phase == 6:
        return validate_phase6_semantic(req_dir, state, semantic_enabled=semantic_enabled)
    return [Issue("gate.unsupported_phase", phase, "FAIL", f"不支持对 P{phase} 跑出口门控")]


# ======================================================================
# 4. Submit quality (8 checks) + deploy plan (9 rules)
# ======================================================================
SUBMIT_SECTIONS = ("背景", "目标", "范围", "验收口径", "风险")


def validate_submit_quality(title: str, description_md: str) -> List[Issue]:
    issues: List[Issue] = []
    if not str(title or "").strip().startswith("【数据需求】"):
        issues.append(Issue("submit.title.topic", 0, "FAIL", "工单主题必须以【数据需求】开头", rule=1))
    missing_sections = [s for s in SUBMIT_SECTIONS if f"## {s}" not in description_md and f"# {s}" not in description_md and s not in description_md]
    if missing_sections:
        issues.append(Issue("submit.desc.sections", 0, "FAIL", f"5 章不齐，缺: {missing_sections}", rule=2))
    for key, code, rule_no in (
        ("目标表", "submit.desc.target", 3),
        ("来源", "submit.desc.source", 4),
        ("验收", "submit.desc.acceptance", 5),
    ):
        if key not in description_md:
            issues.append(Issue(code, 0, "FAIL", f"描述中缺「{key}」要素", rule=rule_no))
    if "负责人" not in description_md and "owner" not in description_md.lower():
        issues.append(Issue("submit.desc.owner", 0, "FAIL", "缺业务负责人", rule=6))
    if not re.search(r"\b(计划|目标)?\s*日期|20\d{2}-\d{2}", description_md):
        issues.append(Issue("submit.desc.plan_date", 0, "FAIL", "缺计划/目标日期", rule=7))
    if len(description_md.strip()) < 100:
        issues.append(Issue("submit.desc.too_short", 0, "FAIL", "描述过短（<100字符），工单质量不足", rule=8))
    return issues


def validate_deploy_plan(req_dir: Path, ticket_id: str) -> List[Issue]:
    issues: List[Issue] = []
    plan_path = req_dir / "deploy_plan.json"
    plan = context.load_json(plan_path)
    if not isinstance(plan, dict):
        issues.append(Issue("deploy_plan.missing", 5, "FAIL", "deploy_plan.json 缺失", file="deploy_plan.json"))
        return issues
    return validate_deploy_plan_dict(plan, ticket_id)


def validate_deploy_plan_dict(plan: Dict[str, Any], ticket_id: str) -> List[Issue]:
    """The 9 deploy-plan rules over an in-memory plan (used by the Write hook)."""
    issues: List[Issue] = []
    if not isinstance(plan, dict):
        issues.append(Issue("deploy_plan.invalid", 5, "FAIL", "deploy_plan 不是对象", file="deploy_plan.json"))
        return issues
    if str(plan.get("ticket_id", "")) != ticket_id:
        issues.append(Issue("deploy_plan.ticket", 5, "FAIL", "ticket_id 与当前工单不一致", rule=1))
    plan_env = str(plan.get("env") or "").lower()
    if plan_env not in ("dev", "test", "qa", "staging", "prod", "production"):
        issues.append(
            Issue(
                "deploy_plan.env",
                5,
                "FAIL",
                f"env 必须是 dev/test/qa/staging/prod/production 之一，当前: {plan.get('env')!r}",
                rule=2,
            )
        )
    items = plan.get("items") if isinstance(plan.get("items"), list) else []
    if not items:
        issues.append(Issue("deploy_plan.empty", 5, "FAIL", "items 为空", rule=3))
    valid_actions = {"create", "alter", "backfill", "dqc_publish"}
    for i, item in enumerate(items):
        table = str(item.get("table", "")).strip()
        action = item.get("action")
        if not table:
            issues.append(Issue("deploy_plan.item_table", 5, "FAIL", f"items[{i}] 缺 table", rule=4))
        if action not in valid_actions:
            issues.append(Issue("deploy_plan.item_action", 5, "FAIL", f"items[{i}] 动作非法: {action}", rule=4))
        if action == "alter":
            if not str(item.get("ddl_diff", "")).strip():
                issues.append(Issue("deploy_plan.alter_diff", 5, "FAIL", "alter 项缺 ddl_diff 路径", rule=5))
        if "rollback_sql_file" in item and not str(item.get("rollback_sql_file", "")).strip():
            issues.append(Issue("deploy_plan.rollback_empty", 5, "FAIL", "rollback_sql_file 不能为空串", rule=5))
        if action == "backfill":
            start, end = item.get("range_start"), item.get("range_end")
            if not start or not end or str(start) > str(end):
                issues.append(Issue("deploy_plan.backfill_range", 5, "FAIL", "backfill 必须给合法 range_start<=range_end", rule=6))
    if plan_env in ("prod", "production") and plan.get("publish_confirmed") is not True:
        issues.append(Issue("deploy_plan.unconfirmed_warn", 5, "WARN", "publish_confirmed=false（WARN：由发布门最终把关）", rule=8))
    return issues


def delivery_checklist(req_dir: Path, state: Dict[str, Any]) -> List[Issue]:
    """P3 -> P4 handover list consumed by the delivery_checklist gate."""
    return validate_phase3_data_dev(req_dir, state)
