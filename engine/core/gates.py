"""PreToolUse / Write decision logic ("hard gates").

These functions answer one question: **may this tool call run?** They are pure
with respect to pipeline state (retry/read ledgers under ``.data-dev/.runtime``
are the only side effects). Claude Code hooks wrap them; unit tests call them
directly. Every decision is derived from machine-readable state + files on
disk, never from what the model claims.

``Decision.action`` is either ``allow`` or ``block``; ``notes`` carries soft
warnings that never block by themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import clients, context, validators
from .state import Pipeline

ACTION_TOKENS = [
    "createTask", "create_task", "create-task", "createtask", "create_issue", "createIssue",
    "updateTask", "update_task", "update-task", "updatetask",
    "readTask", "read_task", "read-task",
    "submit", "publish", "backfill",
    "runSql", "run_sql", "run-sql", "run_sql_query",
    "checkSqlGrammar", "check_sql_grammar", "check-grammar",
    "apply_privilege", "apply-privilege",
    "comment", "add-comment",
    "show", "query-workitem", "query_work_item",
    "advance", "sds_backfill_resolve", "resolve-backfill",
    "platform", "tracker", "quality", "openviking",
]

CLI_NAMES = ("tracker", "platform", "quality", "openviking")


@dataclass
class Decision:
    action: str  # allow | block
    gate: str = ""
    reason: str = ""
    notes: List[str] = field(default_factory=list)

    def blocked(self) -> bool:
        return self.action == "block"


ALLOW = Decision("allow")


def _normalize_cmd(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def analyze_command(command: str) -> Dict[str, Any]:
    """Best-effort structural analysis of a command string."""
    command = _normalize_cmd(command)
    lowered = command.lower()
    found = sorted({tok for tok in ACTION_TOKENS if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(tok)}(?![A-Za-z0-9_-])", command)})
    engine = bool(re.search(r"(?:\.data[-_]dev[\\/]engine[\\/]ddp\.py|ddp\.py|ddp\b)", lowered))
    cli = next((name for name in CLI_NAMES if re.search(rf"(?<![A-Za-z0-9_-]){name}(?![A-Za-z0-9_-])", lowered)), None)
    actions = set()
    for token in found:
        if token in ("platform", "tracker", "quality", "openviking"):
            continue
        actions.add(token)
    return {
        "command": command,
        "lowered": lowered,
        "cli": cli,
        "engine": engine,
        "actions": actions,
        "ticket": _extract_flag(command, ("--ticket", "--id", "-t")),
        "task_name": _extract_flag(command, ("--name", "-n", "--task")),
        "sql_text": _extract_sql(command),
        "sql_file": _extract_flag(command, ("--file", "--sql-file")),
        "comment": _extract_flag(command, ("--comment", "-c", "--message")),
        "config_text": _extract_flag(command, ("--config",)),
        "config_file": _extract_flag(command, ("--config-file", "--plan")),
        "phase_flag": _extract_flag(command, ("--phase", "-p")),
    }


def _extract_flag(command: str, names: Tuple[str, ...]) -> Optional[str]:
    """取 flag 值。

    两个实测坑（Codex 宿主里踩到）：
    * 命令串联（`;` / `&&` / `||`）时，后一条命令会被吞进前一条 flag 的取值里；
    * 短参必须要求前面是空白，否则 `update-task` 里的 `-t` 会被当成 `--ticket`，
      实测把 ticket 解析成了 "ask"。
    """
    for segment in re.split(r";|&&|\|\|", command):
        for name in names:
            m = re.search(
                rf"(?:^|\s){re.escape(name)}=?(['\"]?)(.*?)\1?(?=\s+--|\s+-[A-Za-z]|\s*$)",
                segment,
            )
            if m:
                value = (m.group(2) or "").strip().strip("'\"")
                if value:
                    return value
    return None


def _extract_sql(command: str) -> str:
    m = re.search(r"--sql(?:=| )([\s\S]*?)(?=\s+--|\s*$)", command)
    if not m:
        m = re.search(r"-q(?:=| )([\s\S]*?)(?=\s+--|\s*$)", command)
    return (m.group(1).strip().strip("'\"") if m else "")


def _read_flag_file(root: Path, value: Optional[str]) -> str:
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return context.read_text(path)


def _active_pipeline(root: Path, ticket_arg: Optional[str] = None) -> Optional[Pipeline]:
    try:
        ticket = context.resolve_ticket(root, ticket_arg)
        if not ticket:
            return None
        req = context.ticket_dir(root, ticket)
        pipeline = Pipeline(req)
        return pipeline if pipeline.exists() else None
    except Exception:
        return None


def _kb_scope_block(state: Optional[Dict[str, Any]], command: str, tool_name: str, prefix: str) -> Optional[Decision]:
    """SDS 态下知识源越界即阻断（kb_scope_guard_hook）。"""
    sds_status = ((state or {}).get("sds_design") or {}).get("status")
    if sds_status not in ("in_progress", "review_pending", "review_backfill", "pending_confirm"):
        return None
    text = f"{tool_name} {command}"
    if "viking://" in text:
        uris = re.findall(r"viking://[^\s'\"]+", text)
        bad = [u for u in uris if not u.startswith(prefix)]
        if bad:
            return Decision(
                "block",
                gate="kb_scope_guard",
                reason=f"知识源越界（铁律: 必须以 {prefix} 开头）: {bad[:3]}",
            )
    return None


# ---------------------------------------------------------------------------
# Bash / CLI / MCP gate pipeline (pre_bash_dispatcher)
# ---------------------------------------------------------------------------
# 写文件动词表：覆盖 POSIX shell 与 PowerShell 的常见写法（含重定向）。
_MUTATOR_RE = re.compile(
    r"\b(rm|del|delete|unlink|remove|mv|move|rename|remove-item|removeitem|write_text|write_bytes|shutil"
    r"|set-content|add-content|out-file|new-item|clear-content|copy-item|copy|cp|tee|tee-object|touch|dd)\b"
    r"|sed\s+-i|open\([^)]*['\"]w"
    r"|(?<![-\d&])>>?(?!&)",
    re.IGNORECASE,
)

# 机器资产标记：出现在命令里且与写动词同段时才判违规。
_SENSITIVE_MARKERS = (
    "runtime/",
    "runtime\\",
    ".runtime/",
    ".runtime\\",
    "engine/",
    "engine\\",
    "_state.json",
    "_context.md",
    "autopilot_decisions.md",
)


def _command_segments(command: str) -> List[str]:
    """按 shell 分隔符切段（`;` / `&&` / `||` / 换行）。

    写动词与敏感路径必须在**同一段**里才算命中——否则跨行的分析脚本会被误伤。
    """
    return [seg for seg in re.split(r";|&&|\|\||\n", command.lower()) if seg.strip()]


_VAR_ASSIGN_RE = re.compile(r"\$?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\n]+)")

_ENV_ASSIGN_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
_LAUNCHER_PREFIX_RE = re.compile(r"^(?:sudo|env|nohup|time|command|exec|start|call)\s+", re.IGNORECASE)
_FIRST_TOKEN_RE = re.compile(r"""^\s*(?:&\s*)?(?:"([^"]*)"|'([^']*)'|(\S+))""")


def invoked_cli_names(command: str) -> set:
    """CLI names **actually invoked as a command word**, not merely mentioned.

    实测误伤：``rg -n ... engine/core/sql_platforms.py`` 里的 platform、``rg quality``
    里的 quality，都因为"整条命令做子串匹配"被当成"未 /setup 就裸调平台 CLI"而整条阻断
    （只读分析也拦）。这里只在段首命令词上判定：

    * 算调用：``platform run-sql …`` / ``./platform …`` / ``& "C:\\tools\\quality.exe" …``
      / ``FOO=1 sudo tracker …``
    * 不算调用：出现在搜索串、参数、路径中段（``sql_platforms.py``），或段首是别的命令
    """
    found: set = set()
    for segment in _command_segments(command):
        text = _ENV_ASSIGN_PREFIX_RE.sub("", segment.strip(), count=1)
        text = _LAUNCHER_PREFIX_RE.sub("", text, count=1)
        match = _FIRST_TOKEN_RE.match(text)
        if not match:
            continue
        token = next((group for group in match.groups() if group), "")
        if not token:
            continue
        name = re.split(r"[/\\]", token)[-1]
        name = re.sub(r"\.(exe|cmd|bat|ps1)$", "", name, flags=re.IGNORECASE).lower()
        if name in CLI_NAMES:
            found.add(name)
    return found


def _expand_vars(segments: List[str]) -> List[str]:
    """把简单变量赋值展开到后续段。

    实测绕过：`$t = ...\\.data-dev\\runtime\\x.json` 与 `Set-Content -Path $t`
    分属两段，按段匹配就看不到真实路径了。展开后同段规则依然成立。
    """
    values: Dict[str, str] = {}
    expanded_segments: List[str] = []
    for segment in segments:
        match = _VAR_ASSIGN_RE.search(segment)
        if match:
            values[match.group(1).lower()] = match.group(2)
        expanded = segment
        for name, value in values.items():
            # 用 lambda 做替换：value 里可能含反斜杠（Windows 路径），直接当替换串会被当转义
            expanded = re.sub(rf"\${re.escape(name)}\b", lambda _m, v=value: v, expanded)
        expanded_segments.append(expanded)
    return expanded_segments


def decide_cli_command(command: str, root: Optional[Path] = None) -> Decision:
    """Evaluate one command string against every hard gate, in order."""
    if not command.strip():
        return ALLOW
    root = root or context.project_root() or Path.cwd()
    has_app = (root / context.APP_DIR_NAME / context.CONFIG_FILE).is_file()
    analysis = analyze_command(command)
    actions = analysis["actions"]
    lowered = analysis["lowered"]
    config = context.load_config(root) if has_app else {}
    kb_prefix = clients.kb_prefix(config) if has_app else clients.ALLOWED_KB_PREFIX

    # engine/runtime/state files are machine-owned: any mutation-shaped command is blocked。
    # 注意按 shell 分隔符**切段**判断：写动词与敏感路径必须落在同一段里才算命中。
    # 否则跨行的分析脚本会被误伤（实测被误拦过一次：一行 shutil、另一行里出现 _state.json）。
    segments = _expand_vars(_command_segments(command))
    inside_sensitive_dir = False
    for segment in segments:
        # `cd <机器资产目录>` 之后用相对路径写文件：段里看不到绝对路径，但意图同样明确
        if re.search(r"(^|[;&|]\s*)cd\s", segment) and re.search(
            r"\.data-dev[/\\]+(runtime|\.runtime|engine)([/\\]|\s|$)", segment
        ):
            inside_sensitive_dir = True
            continue
        if not _MUTATOR_RE.search(segment):
            continue
        if inside_sensitive_dir:
            return Decision(
                "block",
                gate="runtime_guard",
                reason="检测到先切入 .data-dev 机器资产目录、再用相对路径写入：按同一条硬门拦截。",
            )
        if ".data-dev" not in segment:
            continue
        marker = next((m for m in _SENSITIVE_MARKERS if m in segment), "")
        if not marker:
            continue
        gate = (
            "runtime_guard"
            if "runtime" in segment
            else ("engine_readonly" if "engine" in segment else "state_write_guard")
        )
        return Decision(
            "block",
            gate=gate,
            reason=f"{gate}: .data-dev 下的 engine/runtime/状态文件是机器资产，禁止在 Bash 中改写/删除。",
        )

    # 同类绕过：shell 直接写工单工作区外的 ddl/etl/dp_task（同样要求同一段）
    for segment in segments:
        if not _MUTATOR_RE.search(segment):
            continue
        if not re.search(r"\b(ddl|etl|dp_task)[\\/]", segment):
            continue
        if (".data-dev/tickets" in segment) or (".data-dev\\tickets" in segment):
            continue
        return Decision(
            "block",
            gate="workspace_guard",
            reason="研发 SQL 目录必须在 .data-dev/tickets/<工单> 工作区内（shell 写同样受管）。",
        )

    # 0. setup_guard: bare platform CLIs before /setup
    # 只认段首命令词，不做整条命令的子串匹配——否则 `rg … sql_platforms.py`
    # 这类只读分析会被误判成裸调平台 CLI（实测被拦过多次）。
    if not has_app and invoked_cli_names(command):
        if "setup" in lowered:
            return ALLOW
        return Decision(
            "block",
            gate="setup_guard",
            reason="未运行 /setup：裸调 tracker/platform/quality 等平台 CLI 前必须先项目初始化。",
        )
    if not has_app:
        return ALLOW

    pipeline = _active_pipeline(root, analysis["ticket"])
    state = pipeline.state if pipeline else {}
    ctx = state.get("context") or {}
    phase = int(state.get("current_phase", 0) or 0)
    sds_status = ((state.get("sds_design")) or {}).get("status")
    awaiting = bool(state.get("awaiting_confirmation", False))
    run_mode = state.get("run_mode", "interactive")

    # kb_scope_guard (SDS)
    kb_decision = _kb_scope_block(state, command, "", kb_prefix)
    if kb_decision:
        return kb_decision

    # 1. confirmation_gate (check_cli): lock writes + prod CLIs while awaiting
    dangerous_actions = {
        "publish", "submit", "update_task", "create_task", "backfill",
        "runSql", "run_sql", "run_sql_query", "platform",
    }
    if awaiting and (actions & dangerous_actions or re.search(r"\b(prod|production)\b", lowered)):
        return Decision(
            "block",
            gate="confirmation_gate",
            reason=(
                f"awaiting_confirmation=true（scope={state.get('confirmation_scope')}）："
                "必须先 AskUserQuestion 完成确认并清除标志，禁止此时写交付物/调生产 CLI。"
            ),
        )

    # 2. phase_entry_guard: create/update_task need workspace + phase >= 3
    if actions & {"create_task", "update_task", "createtask", "updatetask"}:
        if not pipeline:
            return Decision("block", gate="phase_entry_guard", reason="无 workspace/工单状态：禁止建任务。")
        if phase < 3:
            return Decision(
                "block",
                gate="phase_entry_guard",
                reason=f"当前 P{phase} < P3：未到数据研发阶段禁止 create/update_task。",
            )

    # 3. dataprocess_param_guard
    missing = []
    if actions & {"runSql", "run_sql", "run_sql_query"} and not (analysis["sql_text"] or analysis["sql_file"]):
        missing.append("run_sql 需 --sql 或 --file")
    if actions & {"create_task", "update_task"} and not (analysis["config_text"] or analysis["config_file"] or analysis["task_name"]):
        missing.append("create/update_task 需 --name 或 --config")
    if "backfill" in actions and not (analysis["task_name"] and re.search(r"--(start|range_start)", lowered)):
        missing.append("backfill 需 --name 与 --start/--range_start")
    if actions & {"readTask", "read_task"} and not analysis["task_name"]:
        missing.append("read_task 需 --name")
    if missing:
        return Decision("block", gate="dataprocess_param_guard", reason="必填参数缺失: " + "; ".join(missing))

    # 4. retry circuit guards (hash-based, persisted)
    if actions & {"checkSqlGrammar", "check_sql_grammar", "check-grammar"}:
        sql = analysis["sql_text"] or _read_flag_file(root, analysis["sql_file"])
        if sql and _retry_exceeded(root, "grammar_circuit", sql, int(config.get("limits", {}).get("sql_grammar_retry_max", 3))):
            return Decision("block", gate="grammar_retry_circuit", reason="同一 SQL 语法检查 ≥3 次熔断：先查知识库或改 SQL，禁止原地重试。")
    if analysis["cli"] == "platform" and actions & {"create_task", "update_task", "readTask", "read_task"}:
        key = analysis["task_name"] or analysis["command"]
        threshold = 2 if actions & {"readTask", "read_task"} else 3
        if _retry_exceeded(root, "platform_retries", key, threshold):
            return Decision("block", gate="dataprocess_retry_guard", reason=f"platform 重试 ≥{threshold} 次熔断（{key}）：检查权限/依赖，不要原地盲重试。")

    # 5. submit_guard: hard evidence keys must already be passed
    # 注意：引擎自己的 `ddp.py sds publish`（P1 终版贴单）走的是同一套 action token，
    # 但 P1 阶段不可能有 verify/self_check/user_confirmed 三个研发期证据键，
    # 不排除它就会把 SDS 终版发布锁死（实测 Codex 宿主里踩到）。
    sds_publish = "sds" in lowered and "publish" in lowered and "ddp.py" in lowered
    if actions & {"create_task", "update_task", "submit", "publish"} and not sds_publish:
        missing_keys = [
            k for k, expected in (("verify_result", "passed"), ("self_check_result", "passed"), ("user_confirmed", True)) if ctx.get(k) != expected
        ]
        if missing_keys:
            return Decision(
                "block",
                gate="submit_guard",
                reason="证据键未齐，禁止提交/发布: " + ", ".join(f"{k}≠{'passed' if k != 'user_confirmed' else True}" for k in missing_keys)
                + "（需先 SQL 验证 + self_check + 用户确认，状态由验证器写入）。",
            )

    # 6. sql_compliance: 9 zero-tolerance rules over inline/file SQL
    sql_text = analysis["sql_text"] or _read_flag_file(root, analysis["sql_file"])
    if sql_text and not any(tok in lowered for tok in ("check_sql_grammar", "sql check", "ddp.py sql check")):
        req_dir = pipeline.directory if pipeline else None
        ddl_targets = []
        is_task = "dp_task" in lowered
        if req_dir:
            ddl_dir = req_dir / "ddl"
            if ddl_dir.is_dir():
                ddl_targets = [p.stem for p in ddl_dir.glob("*.sql")]
        hard = [i for i in validators.sql_compliance_check(sql_text, filename=analysis["sql_file"] or "", is_dp_task=is_task, ddl_targets=ddl_targets) if i.severity in ("FAIL", "HALTED")]
        if hard:
            return Decision("block", gate="sql_compliance", reason="SQL 零容忍规则违规: " + "; ".join(str(h) for h in hard[:3]))

    # 7. backfill_guard: 写操作必须先读字段（2h TTL）
    if "backfill" in actions or sql_text:
        targets = []
        if sql_text:
            targets = clients.parse_sql_write_targets(sql_text)
        elif analysis["task_name"]:
            targets = [analysis["task_name"]]
        unread = [t for t in targets if not _was_read(root, t)]
        if unread and not (actions & {"readTask", "read_task", "query", "select"}):
            return Decision(
                "block",
                gate="backfill_guard",
                reason=f"写操作未先读字段（2h TTL 内无读取记录）: {unread[:3]}。先只读核对目标表结构/数据。",
            )

    # 8. update_task_read_guard: update 前必须 read 原代码（P5 豁免）
    if actions & {"update_task", "updatetask"} and phase != 5:
        task = analysis["task_name"]
        if task and not _was_read(root, task):
            return Decision("block", gate="update_task_read_guard", reason=f"update_task({task}) 前必须先 read 原代码（update_task_read_guard）。")

    # 9. deploy_task_check: 5 条部署配置规则
    if actions & {"create_task", "update_task"} and (analysis["config_text"] or analysis["config_file"]):
        config_text = analysis["config_text"] or _read_flag_file(root, analysis["config_file"])
        try:
            cfg = json.loads(config_text) if isinstance(config_text, str) else {}
        except json.JSONDecodeError:
            cfg = {}
        problems = _deploy_config_problems(root, pipeline, analysis, cfg)
        if problems:
            return Decision("block", gate="deploy_task_check", reason="部署配置违规: " + "; ".join(problems))

    # 10. publish_gate: [已确认发布] token + autopilot 的 diff/report
    # 同样排除引擎自己的 `ddp.py sds publish`：P1 终版贴单不是生产发布，
    # 不该要求 [已确认发布] token / code_diff / review_report（实测 Codex 里被锁死过）。
    sds_publish = "sds" in lowered and "publish" in lowered and "ddp.py" in lowered
    if "publish" in actions and not sds_publish:
        req_dir = pipeline.directory if pipeline else None
        comment = analysis["comment"] or ""
        plan = {}
        if req_dir and (req_dir / "deploy_plan.json").is_file():
            plan = context.load_json(req_dir / "deploy_plan.json") or {}
        plan_env = str(plan.get("env") or "").lower()
        is_prod = plan_env in ("prod", "production") if plan_env else True
        problems = []
        if is_prod and "[已确认发布]" not in comment:
            problems.append("comment 必须含 [已确认发布] token")
        baseline_modified = bool(ctx.get("code_diff.required"))
        if req_dir:
            if baseline_modified and not (req_dir / "code_diff.html").is_file():
                problems.append("改动现有任务必须先生成并展示 code_diff.html（盲签发布被堵）")
            if is_prod and run_mode == "autopilot" and not (req_dir / "review_report.html").is_file():
                problems.append("autopilot 发布前必须有 review_report.html 审查报告")
        if problems:
            return Decision("block", gate="publish_gate", reason="发布门未过: " + "; ".join(problems))

    # 11. delivery_checklist (P3 -> P4) + review_report_gate (advance -> P5)
    if "advance" in actions:
        target_phase = analysis["phase_flag"]
        if target_phase == "4" and pipeline:
            problems = validators.blockers(validators.delivery_checklist(pipeline.directory, state))
            if problems:
                return Decision(
                    "block",
                    gate="delivery_checklist",
                    reason="P3 -> P4 交付清单未完成: " + "; ".join(str(p) for p in problems[:5]),
                )
        if target_phase == "5" and run_mode == "autopilot" and pipeline and not (pipeline.directory / "review_report.html").is_file():
            return Decision(
                "allow",
                gate="review_report_gate",
                reason="autopilot 预生成 review_report.html 缺失（软提醒，不阻断推进）",
                notes=["review_report_gate: autopilot 推进到 P5 前建议先生成 review_report.html"],
            )

    return ALLOW


def _deploy_config_problems(root: Path, pipeline: Optional[Pipeline], analysis: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    """deploy_task_check: 5 条规则（DDL+ETL 共存/调度匹配/任务名=表名/代码=dp_task/禁混ALTER）。"""
    problems: List[str] = []
    task_name = analysis["task_name"] or cfg.get("task_name") or cfg.get("name")
    target_table = cfg.get("target_table") or cfg.get("table")
    if task_name and target_table and str(task_name).split(".")[-1] != str(target_table).split(".")[-1]:
        problems.append(f"任务名 {task_name} != 表名 {target_table}")
    if cfg.get("schedule") or analysis.get("sql_file"):
        pass
    if not str(cfg.get("schedule", "")).strip():
        problems.append("dp_task 缺调度（schedule）")
    if not pipeline:
        problems.append("无 workspace，无法核对代码/DDL")
        return problems
    stem = str(target_table or task_name or "").split(".")[-1]
    if stem:
        if not (pipeline.directory / "ddl" / f"{stem}.sql").is_file():
            problems.append(f"ddl/{stem}.sql 缺失（DDL 必须先于 ETL）")
        if not (pipeline.directory / "etl" / f"{stem}.sql").is_file():
            problems.append(f"etl/{stem}.sql 缺失")
        if not (pipeline.directory / "dp_task" / f"{stem}.sql").is_file():
            problems.append(f"dp_task/{stem}.sql 缺失")
    return problems


def _ledger_append(root: Path, name: str, key: str) -> None:
    holder = context.touch_runtime_ledger(root, name)
    bucket = holder["data"].setdefault(key, [])
    bucket.append(time.time())
    context.save_runtime_ledger(holder["path"], holder["data"])


def _ledger_recent_count(root: Path, name: str, key: str, ttl_seconds: float = 3600.0) -> int:
    holder = context.touch_runtime_ledger(root, name)
    bucket = holder["data"].setdefault(key, [])
    cutoff = time.time() - ttl_seconds
    fresh = [ts for ts in bucket if ts >= cutoff]
    if len(fresh) != len(bucket):
        holder["data"][key] = fresh
        context.save_runtime_ledger(holder["path"], holder["data"])
    return len(fresh)


def _retry_exceeded(root: Path, ledger: str, key: str, threshold: int) -> bool:
    digest = hashlib.sha256(key.encode()).hexdigest()
    if _ledger_recent_count(root, ledger, digest) >= threshold:
        return True
    _ledger_append(root, ledger, digest)
    return False


def mark_read(root: Path, table_or_task: str) -> None:
    """Called by PostToolUse when a read tool actually ran."""
    _ledger_append(root, "reads", table_or_task)


def _was_read(root: Path, table_or_task: str) -> bool:
    ttl = float((context.load_config(root).get("limits") or {}).get("read_cache_ttl_seconds", 7200))
    return _ledger_recent_count(root, "reads", table_or_task, ttl) > 0


# ---------------------------------------------------------------------------
# Write / Edit gate pipeline
# ---------------------------------------------------------------------------
PHASE_FILE_MAP = [
    ("sds_design.json", {1}),
    ("evidence.json", {1}),
    ("verification", {1}),
    ("sds_design_spec.md", {1}),
    ("architecture_doc.md", {2}),
    ("pipeline.html", {2}),
    ("etl_dev_exec.json", {3, 4}),
    ("test_report.md", {4}),
    ("test_runs.json", {4}),
    ("delivery_summary.md", {5}),
    ("deploy_log.json", {5}),
    ("deploy_plan.json", {5}),
    ("semantic_update_summary.md", {6}),
]


def _normalize_component(part: str) -> str:
    """归一化 Windows 路径分量：尾随点/空格会被文件系统忽略，`runtime.` == `runtime`。

    只用于比较，不改真实路径；对 `.` / `..` 这类已由 resolve() 处理的分量保持原样。
    """
    if part in (".", "..", "/", "\\", ""):
        return part
    return part.rstrip(" .") or part


def decide_file_write(file_path: str, content: str, root: Optional[Path] = None) -> Decision:
    """Gate a Write/Edit whose target lives inside a ticket workspace."""
    if not file_path:
        return ALLOW
    root = root or context.project_root() or Path.cwd()
    has_app = (root / context.APP_DIR_NAME / context.CONFIG_FILE).is_file()
    kb_prefix = clients.kb_prefix(context.load_config(root)) if has_app else clients.ALLOWED_KB_PREFIX
    modeling = (context.load_config(root) if has_app else context.default_config()).get("modeling") or {}
    path = Path(str(file_path).strip())
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()
    ticket_dir = None
    try:
        rel = path.relative_to(root.resolve())
    except ValueError:
        rel = Path(path.name)
    # Windows 的路径归一化怪癖：每个分量上的尾随点与空格会被文件系统忽略
    # （`runtime.` 就是 `runtime`、`_state.json.` 就是 `_state.json`），
    # 不归一化就能用这类等价写法绕过硬门。
    parts = [_normalize_component(p).lower() for p in path.parts]

    # locate owning ticket dir (deepest .data-dev/tickets/<id> ancestor)
    for idx, part in enumerate(parts):
        if part == ".data-dev" and idx + 2 < len(parts) and parts[idx + 1] == "tickets":
            ticket_dir = Path(*path.parts[: idx + 3])
            break
    if ticket_dir is None:
        if any(seg in parts for seg in ("ddl", "etl", "dp_task")):
            return Decision("block", gate="workspace_guard", reason="研发 SQL 目录必须在 .data-dev/tickets/<工单> 工作区内。")
        if ".data-dev" in parts:
            app_idx = parts.index(".data-dev")
            if app_idx + 1 < len(parts) and parts[app_idx + 1] == "engine":
                return Decision("block", gate="engine_readonly", reason=".data-dev/engine 是引擎运行时副本，禁止工具改写（engine_readonly）。")
            if app_idx + 1 < len(parts) and parts[app_idx + 1] in ("runtime", context.RUNTIME_DIR):
                return Decision("block", gate="runtime_guard", reason=".data-dev/runtime 是机器账本/运行时目录，只允许引擎写入。")
        return ALLOW

    pipeline = Pipeline(ticket_dir)
    if not pipeline.exists():
        return Decision(
            "block",
            gate="ticket_not_initialized",
            reason=f"{ticket_dir} 没有 _state.json：先 /submit 或 Pipeline.init 初始化，禁止在未初始化工单目录写交付物。",
        )
    state = pipeline.state
    phase = pipeline.phase
    sds_status = pipeline.sds.get("status")
    rel_to_ticket = path.relative_to(ticket_dir)
    # 统一成正斜杠：Windows 上 str(Path) 是 "verification\\q1.sql"，
    # 会让 PHASE_FILE_MAP 里的 "verification/" 前缀判断失效（阶段门被绕过）。
    # 同时对每个分量做 Windows 尾随点/空格归一化：`_state.json.` 就是 `_state.json`。
    rel_lower = "/".join(_normalize_component(p).lower() for p in rel_to_ticket.parts)

    # machine state files: only the engine may write them (no tool-call backdoor)
    if rel_lower in ("_state.json", "_context.md", "autopilot_decisions.md"):
        return Decision(
            "block",
            gate="state_write_guard",
            reason=f"{rel_to_ticket} 是机器状态文件，只允许引擎写入；工具 Write 被拦截。",
        )

    # confirmation_gate_hook: awaiting_confirmation locks every deliverable write
    if state.get("awaiting_confirmation") and ".runtime" not in rel_lower:
        return Decision(
            "block",
            gate="confirmation_gate_hook",
            reason=f"awaiting_confirmation=true（scope={state.get('confirmation_scope')}），Write 被锁: {rel_to_ticket}",
        )

    # stage / phase ownership for known deliverables
    for name, allowed_phases in PHASE_FILE_MAP:
        if rel_lower == name or rel_lower.startswith(name.lower() + "/"):
            if phase not in allowed_phases:
                return Decision(
                    "block",
                    gate="phase_guard",
                    reason=f"写 {rel_to_ticket} 需要阶段 {sorted(allowed_phases)}，当前 P{phase}。",
                )

    # SDS knowledge uri discipline on sds_design.json writes
    if rel_lower == "sds_design.json":
        try:
            obj = json.loads(content)
        except json.JSONDecodeError:
            return Decision("block", gate="sds_json_valid", reason="sds_design.json 不是合法 JSON，禁止写入。")
        uri = str(obj.get("knowledge_source_uri", ""))
        if not uri.startswith(kb_prefix):
            return Decision(
                "block",
                gate="kb_scope_guard_hook",
                reason=f"knowledge_source_uri 越界: {uri!r}（必须以 {kb_prefix} 开头）",
            )

    # dev_order_guard + difficulty_guard + phase gating for ddl/etl/dp_task
    rel_parts = rel_to_ticket.parts
    if len(rel_parts) >= 2 and rel_parts[0] in ("ddl", "etl", "dp_task"):
        sub, filename = rel_parts[0], rel_parts[-1]
        if not filename.lower().endswith(".sql"):
            return Decision("block", gate="dev_order_guard", reason=f"{sub}/ 只允许 .sql 交付物: {filename}")
        stem = Path(filename).stem
        level = pipeline.level
        if phase not in (3, 5):
            return Decision("block", gate="phase_entry_guard", reason=f"写 {sub}/{filename} 需要 P3（当前 P{phase}）。")
        if level in ("L0", "L1") and re.search(r"\bCREATE\s+TABLE\b", content, re.IGNORECASE):
            return Decision("block", gate="difficulty_guard", reason=f"L{level} 禁建表：ddl 目录 CREATE TABLE 被难度门拦截（只允许改存量）。")
        if sub == "etl" and not (ticket_dir / "ddl" / f"{stem}.sql").is_file():
            return Decision("block", gate="dev_order_guard", reason=f"ETL 先于 DDL：写 etl/{filename} 前必须有 ddl/{stem}.sql。")
        if sub == "dp_task" and not (ticket_dir / "etl" / f"{stem}.sql").is_file():
            return Decision("block", gate="dev_order_guard", reason=f"dp_task 先于 ETL：写 dp_task/{filename} 前必须有 etl/{stem}.sql。")
        if level == "L3":
            if str(modeling.get("layer_policy", "enabled")) != "disabled":
                prefixes = modeling.get("prefixes") or ("ods", "dwd", "dws", "ads", "dim", "mid")
                prefix = stem.split("_")[0] if "_" in stem else ""
                if prefix not in prefixes:
                    return Decision(
                        "block",
                        gate="difficulty_guard",
                        reason=f"L3 新建分层应用：文件名需带分层前缀（{'/'.join(prefixes)}），当前 {filename}。",
                    )

    # deploy_plan.json 9-rule validation on write
    if rel_lower == "deploy_plan.json":
        try:
            plan = json.loads(content)
        except json.JSONDecodeError:
            return Decision("block", gate="deploy_plan_check_hook", reason="deploy_plan.json 不是合法 JSON。")
        problems = validators.blockers(validators.validate_deploy_plan_dict(plan, pipeline.ticket))
        if problems:
            return Decision("block", gate="deploy_plan_check_hook", reason="deploy_plan 9 条规则违规: " + "; ".join(str(p) for p in problems[:4]))

    # test evidence dir writes require P4+ (files listed above cover main artifacts)
    return ALLOW


def _as_path_text(value: Any) -> str:
    """把宿主可能给出的各种"路径形态"取成字符串（list/dict/None 都能兜住）。"""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = next(iter(value.values()), "")
    return str(value or "").strip()


_MACHINE_ASSET_MARKERS = (
    ".data-dev/runtime",
    ".data-dev\\runtime",
    ".data-dev/.runtime",
    ".data-dev\\.runtime",
    ".data-dev/engine",
    ".data-dev\\engine",
    "_state.json",
    "_context.md",
    "autopilot_decisions.md",
)


def _shape_fallback(tool_input: Any, root: Optional[Path]) -> Optional[Decision]:
    """形状兜底：宿主给了没见过/缺失的工具名时，只要载荷里有"写哪个文件/跑什么命令"的形状，照样过门。"""
    if isinstance(tool_input, dict):
        candidate = _as_path_text(
            tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
        )
        if candidate:
            return decide_file_write(candidate, "", root)
        command = tool_input.get("command") or tool_input.get("cmd")
        if command:
            return decide_cli_command(str(command), root)
        return None
    text = str(tool_input or "")
    if any(marker in text.lower() for marker in _MACHINE_ASSET_MARKERS):
        # 载荷形状异常却提到机器资产：宁可拦住（这是唯一一处 fail-closed 的地方）
        return Decision("block", gate="shape_guard", reason="载荷形状异常但提到机器资产目录，按保守策略拦截。")
    return None


def decide_pre_tool(payload: Dict[str, Any], root: Optional[Path] = None) -> Decision:
    """Main PreToolUse dispatcher used by hooks/pretooluse.py.

    同时支持两个宿主：

    * Claude Code：``Bash`` / ``Write`` / ``Edit`` / ``MultiEdit`` …
    * Codex：``exec_command`` / ``local_shell`` / ``shell_command``（命令），
      ``apply_patch``（补丁式写文件，input 里是 ``*** Add File: <path>`` 形式的补丁文本）

    宿主差异只在"怎么从 payload 里取出路径与命令"，取出来的是同一套 decide_* 门控。
    """
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    raw_input = payload.get("tool_input") or payload.get("input") or {}
    shape_anomaly = not isinstance(raw_input, dict)
    tool_input = {"command": str(raw_input)} if shape_anomaly else raw_input
    if shape_anomaly:
        # 载荷形状异常（不是对象）时先按形状兜底：提到机器资产就拦住，别让下面的
        # 具名分支因为取不到 file_path 而直接放行。
        fallback = _shape_fallback(raw_input, root)
        if fallback is not None:
            return fallback
    if tool_name in ("Bash", "Task", "ExecuteCommand", "CLI") or tool_name.lower() in (
        "exec_command",
        "local_shell",
        "shell_command",
        "shell",
    ):
        command = str(tool_input.get("command") or tool_input.get("cmd") or "")
        decision = decide_cli_command(command, root)
        return decision
    if tool_name.lower() in ("apply_patch", "applypatch", "patch", "edit_file", "write_file"):
        patch_text = str(
            tool_input.get("patch")
            or tool_input.get("input")
            or tool_input.get("content")
            or tool_input.get("text")
            # Codex 实测：apply_patch 的补丁文本放在 tool_input["command"]，且路径是相对路径
            or tool_input.get("command")
            or ""
        )
        targets = _patch_targets(patch_text)
        if not targets and tool_input:
            targets = _patch_targets(json.dumps(tool_input, ensure_ascii=False))
        for target in targets:
            decision = decide_file_write(target, _patch_file_content(patch_text, target), root)
            if decision.blocked():
                return decision
        return ALLOW
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        file_path = _as_path_text(
            tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
        )
        content = str(tool_input.get("content") or tool_input.get("new_string") or tool_input.get("new_content") or "")
        return decide_file_write(file_path, content, root)
    # MCP-like tools (tracker_*, platform_*, quality_*, knowledge_*, openviking_*, mcp__*)
    lowered = tool_name.lower()
    if any(name in lowered for name in ("tracker", "platform", "quality", "knowledge", "viking")):
        serialized = json.dumps(tool_input, ensure_ascii=False) if tool_input else ""
        command = f"{tool_name} {serialized}"
        decision = decide_cli_command(command, root)
        return decision
    if tool_name == "AskUserQuestion":
        return ALLOW
    fallback = _shape_fallback(raw_input if shape_anomaly else tool_input, root)
    return fallback if fallback is not None else ALLOW


_PATCH_TARGET_RE = re.compile(
    r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$"      # *** Add File: <path>
    r"|^\*\*\*\s+Move to:\s*(.+?)\s*$",                          # *** Move to: <path>
    re.MULTILINE,
)


def _patch_file_content(patch_text: str, target: str) -> str:
    """从 Codex ``apply_patch`` 补丁里取出 ``target`` 这个文件的新内容。

    补丁段落形如::

        *** Add File: .data-dev/tickets/T-0001/sds_design.json
        +{"a": 1}
        *** End Patch

    直接把整段补丁当文件内容会让内容类校验（如 sds_design.json 必须是合法 JSON）
    必然误判——实测 Codex 里就因此被错误拦截过。这里剥掉 ``+`` 前缀只留文件内容；
    找不到对应段落时回退为原文。
    """
    if not patch_text:
        return ""
    collecting = False
    collected: List[str] = []
    for line in patch_text.splitlines():
        if line.startswith("*** "):
            if collecting:
                break
            collecting = target in line and any(
                marker in line for marker in ("Add File:", "Update File:", "Move to:")
            )
            continue
        if collecting:
            collected.append(line[1:] if line.startswith("+") else line)
    return "\n".join(collected) if collected else patch_text


def _patch_targets(patch_text: str) -> List[str]:
    """Extract file paths from a Codex ``apply_patch`` payload.

    Codex 的补丁体是 ``*** Begin Patch`` / ``*** Add File: <path>`` / ``*** Update File: <path>``
    这种格式，也可能带 ``+++ b/<path>`` 形式的 diff 头；两种都抽取。
    """
    if not patch_text:
        return []
    # ``*** Move to:`` 的目标路径也必须纳入检查：否则可以用 Update+Move 把文件"搬进"
    # .data-dev/runtime 等受保护目录（对抗测试实测绕过过一次）。
    targets = [(m.group(1) or m.group(2) or "").strip() for m in _PATCH_TARGET_RE.finditer(patch_text)]
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            targets.append(line[6:].strip())
    seen: List[str] = []
    for item in targets:
        if item and item not in seen:
            seen.append(item)
    return seen


def soft_context_note(issues: List[validators.Issue]) -> str:
    if not issues:
        return ""
    lines = ["[data-development-plugin 校验提示]", ""]
    for issue in issues[:10]:
        lines.append(f"- {issue}")
    return "\n".join(lines)
