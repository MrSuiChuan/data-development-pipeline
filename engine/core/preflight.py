"""Pre-development mode selection, ledger and goal.

The plugin has two run modes:

* ``interactive`` — the agent asks the user for the key reversible decisions.
* ``autopilot`` — reversible decisions are delegated to the agent, but the
  irreversible production gates and the semantic-layer entry gate still stop.

Both modes are recorded before development starts.  ``start`` refuses to enter
P2 unless a mode was selected and a goal was created for the ticket.  The
ledger is append-only in spirit: every selection / goal creation gets a new
entry, while the per-ticket snapshots (``_mode.json`` / ``_goal.json``) point at
the current choice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from . import context

MODES = ("interactive", "autopilot")
MODE_LABELS = {
    "interactive": "确认模式",
    "autopilot": "自动模式",
}

LEDGER_FILE = "preflight_ledger.json"
MODE_FILE = "_mode.json"
GOAL_FILE = "_goal.json"
GOAL_MD = "_goal.md"


class PreflightError(RuntimeError):
    """A blocking preflight failure with a stable machine-readable gate."""

    def __init__(self, gate: str, reason: str, **extra: Any) -> None:
        super().__init__(reason)
        self.gate = gate
        self.reason = reason
        self.extra = extra

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": False,
            "blocked": True,
            "gate": self.gate,
            "reason": self.reason,
        }
        payload.update(self.extra)
        return payload


def _ticket_state(root: Path, ticket: str) -> tuple:
    req = context.ticket_dir(root, ticket)
    state = context.load_json(context.state_path(req), {}) or {}
    return req, state


def _ticket_hash(state: Dict[str, Any]) -> str:
    # ``state["ticket_hash"]`` is overloaded: after ``sds bridge`` it becomes the
    # SDS fingerprint (body + target table + mappings + SQL validation), not the
    # original ticket body hash.  Mode/goal staleness must follow the user-facing
    # ticket text, otherwise choosing a mode before SDS would always look stale
    # at ``start``.  Fall back to the state hash only for legacy records without
    # a stored description.
    description = str(state.get("description_md") or "")
    if description:
        return context.sha256_text(description)
    return str(state.get("ticket_hash") or "")


def _ledger(root: Path) -> tuple:
    path = context.ledger_dir(root) / LEDGER_FILE
    data = context.load_json(path, {}) or {}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        data = {"schema_version": 1, "next_entry": 1, "next_goal": 1, "entries": []}
    data.setdefault("next_entry", 1)
    data.setdefault("next_goal", 1)
    return path, data


def _next_entry_id(ledger: Dict[str, Any]) -> str:
    return "P-%04d" % int(ledger.get("next_entry") or 1)


def load_mode(root: Path, ticket: str) -> Optional[Dict[str, Any]]:
    req = context.ticket_dir(root, ticket)
    data = context.load_json(req / MODE_FILE, {}) or {}
    if isinstance(data, dict) and data.get("mode") in MODES:
        return data
    return None


def select_mode(
    root: Path,
    ticket: str,
    mode: str,
    chosen_by: str,
    *,
    source: str = "user_chat",
    reason: str = "",
) -> Dict[str, Any]:
    """Record the user's explicit mode choice before development starts."""
    mode = str(mode or "").strip().lower()
    if mode not in MODES:
        raise PreflightError(
            "mode_choice",
            "运行模式必须是 interactive（确认模式）或 autopilot（自动模式）。",
        )
    chosen_by = str(chosen_by or "").strip()
    if not chosen_by:
        raise PreflightError(
            "mode_choice",
            "模式选择必须记录 --chosen-by（用户在对话中的确认人/来源），不能由 AI 自签。",
        )
    req, state = _ticket_state(root, ticket)
    if not state:
        raise PreflightError("ticket_not_initialized", "工单未初始化：先 /submit 建单。")
    phase = int(state.get("current_phase") or 0)
    if phase >= 2:
        raise PreflightError(
            "mode_locked",
            "工单已进入研发态，运行模式已锁定；模式必须在 /start 之前选择。",
        )

    current_hash = _ticket_hash(state)
    current = load_mode(root, ticket)
    if current and current.get("mode") == mode and current.get("ticket_hash") == current_hash:
        return {"ok": True, "reused": True, "mode": current}

    path, ledger = _ledger(root)
    entry_id = _next_entry_id(ledger)
    entry = {
        "entry_id": entry_id,
        "kind": "mode",
        "ticket": ticket,
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "chosen_by": chosen_by,
        "source": str(source or "user_chat"),
        "reason": str(reason or ""),
        "chosen_at": context.localnow(),
        "ticket_hash": current_hash,
        "previous_entry_id": (current or {}).get("entry_id"),
        "status": "active",
    }
    ledger["entries"].append(entry)
    ledger["next_entry"] = int(ledger.get("next_entry") or 1) + 1
    context.write_json(path, ledger)
    context.write_json(req / MODE_FILE, entry)
    return {"ok": True, "mode": entry, "ledger": str(path), "file": str(req / MODE_FILE)}


def require_mode(root: Path, ticket: str, *, autopilot_flag: bool = False) -> Dict[str, Any]:
    """Return the selected mode or raise a blocking preflight error."""
    mode = load_mode(root, ticket)
    if not mode:
        raise PreflightError(
            "mode_choice_required",
            "开工前必须先让用户选择运行模式（interactive 确认模式 / autopilot 自动模式），"
            "再运行 mode select 记录台账。",
        )
    req, state = _ticket_state(root, ticket)
    current_hash = _ticket_hash(state)
    if mode.get("ticket_hash") and mode["ticket_hash"] != current_hash:
        raise PreflightError(
            "mode_stale",
            "模式台账绑定的工单 hash 与当前工单不一致：工单内容已变更，请让用户重新选择模式并更新台账。",
        )
    if autopilot_flag and mode.get("mode") != "autopilot":
        raise PreflightError(
            "mode_mismatch",
            "命令带了 --autopilot，但台账记录的是 interactive。请去掉该参数，或让用户重新选择模式。",
        )
    return mode


def _sections(description: str) -> Dict[str, str]:
    wanted = ("背景", "目标", "范围", "验收口径", "风险")
    aliases = {
        "验收": "验收口径",
        "验收标准": "验收口径",
        "验收方式": "验收口径",
    }
    collected: Dict[str, list] = {name: [] for name in wanted}
    current: Optional[str] = None
    for raw in str(description or "").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            title = aliases.get(title, title)
            current = title if title in wanted else None
            continue
        if current and line:
            collected[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in collected.items()}


def _target_table(sds: Dict[str, Any], description: str) -> str:
    target = sds.get("target_table") or {}
    if isinstance(target, dict):
        name = str(target.get("name") or "").strip()
        if name:
            return name
    match = re.search(r"目标表\s*[:：]\s*[`\"']?([A-Za-z0-9_.]+)", str(description or ""))
    return match.group(1) if match else ""


def render_goal(goal: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "# 开发 Goal — %s" % goal.get("ticket", ""),
            "",
            "- Goal ID: %s" % goal.get("goal_id", ""),
            "- 工单: %s · %s" % (goal.get("ticket", ""), goal.get("title", "")),
            "- 运行模式: %s（%s）" % (goal.get("mode", ""), MODE_LABELS.get(str(goal.get("mode")), "")),
            "- 模式台账: %s" % goal.get("mode_entry_id", ""),
            "- 目标表: %s" % (goal.get("target_table") or "未指定"),
            "- 难度: %s" % (goal.get("level") or "待定"),
            "- 工单 hash: %s" % goal.get("ticket_hash", ""),
            "",
            "## 目标",
            "",
            str(goal.get("objective") or ""),
            "",
            "## 范围",
            "",
            str(goal.get("scope") or ""),
            "",
            "## 验收口径",
            "",
            str(goal.get("acceptance") or ""),
            "",
            "## 风险",
            "",
            str(goal.get("risks") or ""),
            "",
            "## 阶段出口必须提供的机器证据",
            "",
            "- P3: `etl_dev_exec.json` 中 SUCCESS + query_id",
            "- P4: `test_runs.json` 中每个 ✅ 用例都有 SUCCESS + query_id",
            "- P5: `deploy_log.json` + `delivery_summary.md`",
            "- 生产不可逆门: `[已确认发布]` token；interactive / autopilot 都不旁路",
            "",
            "> 本文件由 data-development-plugin 生成；goal 只定义交付目标与验收边界，"
            "不替代阶段门控和真实执行证据。",
            "",
        ]
    )


def load_goal(root: Path, ticket: str) -> Optional[Dict[str, Any]]:
    req = context.ticket_dir(root, ticket)
    data = context.load_json(req / GOAL_FILE, {}) or {}
    return data if isinstance(data, dict) and data.get("goal_id") else None


def create_goal(root: Path, ticket: str, *, replace: bool = False) -> Dict[str, Any]:
    """Create the per-ticket development goal, bound to the selected mode."""
    mode = load_mode(root, ticket)
    if not mode:
        raise PreflightError(
            "mode_choice_required",
            "创建 goal 之前必须先选择运行模式并写入模式台账。",
        )
    req, state = _ticket_state(root, ticket)
    if not state:
        raise PreflightError("ticket_not_initialized", "工单未初始化：先 /submit 建单。")
    phase = int(state.get("current_phase") or 0)
    if phase >= 2:
        raise PreflightError(
            "goal_locked",
            "工单已进入研发态，goal 已锁定；如需重开目标，请先回退到 P1 并让用户重新确认。",
        )
    current_hash = _ticket_hash(state)
    if mode.get("ticket_hash") and mode["ticket_hash"] != current_hash:
        raise PreflightError(
            "mode_stale",
            "模式台账绑定的是旧工单内容；请先重新选择模式，再创建 goal。",
        )

    existing = context.load_json(req / GOAL_FILE, {}) or {}
    if existing and not replace:
        if existing.get("ticket_hash") == current_hash and existing.get("mode") == mode.get("mode"):
            return {"ok": True, "reused": True, "goal": existing}
        raise PreflightError(
            "goal_stale",
            "已有 goal 与当前工单/模式不一致；确认目标变化后使用 goal create --replace。",
        )

    sds = context.load_json(req / "sds_design.json", {}) or {}
    sections = _sections(str(state.get("description_md") or ""))
    target = _target_table(sds, str(state.get("description_md") or ""))
    level = state.get("level") or sds.get("level_suggestion") or ""

    path, ledger = _ledger(root)
    goal_no = int(ledger.get("next_goal") or 1)
    goal_id = "G-%04d" % goal_no
    goal = {
        "goal_id": goal_id,
        "ticket": ticket,
        "title": state.get("title") or ticket,
        "mode": mode.get("mode"),
        "mode_entry_id": mode.get("entry_id"),
        "ticket_hash": current_hash,
        "objective": sections.get("目标") or state.get("title") or ticket,
        "scope": sections.get("范围") or "",
        "acceptance": sections.get("验收口径") or "",
        "background": sections.get("背景") or "",
        "risks": sections.get("风险") or "",
        "target_table": target,
        "level": level,
        "created_at": context.localnow(),
        "status": "active",
    }
    context.write_json(req / GOAL_FILE, goal)
    context.write_text(req / GOAL_MD, render_goal(goal))

    entry = {
        "entry_id": _next_entry_id(ledger),
        "kind": "goal",
        "ticket": ticket,
        "goal_id": goal_id,
        "mode": mode.get("mode"),
        "ticket_hash": current_hash,
        "created_at": goal["created_at"],
        "status": "active",
    }
    ledger["entries"].append(entry)
    ledger["next_entry"] = int(ledger.get("next_entry") or 1) + 1
    ledger["next_goal"] = goal_no + 1
    context.write_json(path, ledger)
    return {
        "ok": True,
        "goal": goal,
        "ledger": str(path),
        "goal_file": str(req / GOAL_FILE),
        "goal_md": str(req / GOAL_MD),
    }


def require_goal(root: Path, ticket: str, mode: Dict[str, Any]) -> Dict[str, Any]:
    goal = load_goal(root, ticket)
    if not goal:
        raise PreflightError(
            "goal_required",
            "开工前必须先创建本工单 goal（goal create），把目标、范围和验收口径固定下来。",
        )
    req, state = _ticket_state(root, ticket)
    current_hash = _ticket_hash(state)
    if goal.get("ticket_hash") != current_hash:
        raise PreflightError(
            "goal_stale",
            "goal 绑定的工单内容已变更；请让用户确认新目标后重新创建 goal。",
        )
    if goal.get("mode") != mode.get("mode"):
        raise PreflightError(
            "goal_stale",
            "goal 的运行模式与模式台账不一致；请按当前模式重新创建 goal。",
        )
    if str(goal.get("status") or "active") != "active":
        raise PreflightError("goal_stale", "goal 不是 active 状态，请重新创建。")
    return goal


def enrich_goal(
    root: Path,
    ticket: str,
    *,
    sds: Optional[Dict[str, Any]] = None,
    level: Optional[str] = None,
    target_table: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fill in SDS/level details after admission without changing the goal ID."""
    req = context.ticket_dir(root, ticket)
    goal = context.load_json(req / GOAL_FILE, {}) or {}
    if not isinstance(goal, dict) or not goal.get("goal_id"):
        return None
    changed = False
    if sds:
        target = _target_table(sds, str(goal.get("objective") or ""))
        if target and target != goal.get("target_table"):
            goal["target_table"] = target
            changed = True
    if level and level != goal.get("level"):
        goal["level"] = level
        changed = True
    if target_table and target_table != goal.get("target_table"):
        goal["target_table"] = target_table
        changed = True
    if changed:
        goal["updated_at"] = context.localnow()
        context.write_json(req / GOAL_FILE, goal)
        context.write_text(req / GOAL_MD, render_goal(goal))
    return goal


def summary(root: Path, ticket: str) -> Dict[str, Any]:
    mode = load_mode(root, ticket)
    goal = load_goal(root, ticket)
    ready = bool(mode and goal and mode.get("mode") == goal.get("mode")
                 and mode.get("ticket_hash") == goal.get("ticket_hash"))
    return {
        "mode": mode,
        "goal": goal,
        "ready": ready,
        "ledger": str(context.ledger_dir(root) / LEDGER_FILE),
        "mode_file": str(context.ticket_dir(root, ticket) / MODE_FILE),
        "goal_file": str(context.ticket_dir(root, ticket) / GOAL_FILE),
        "goal_md": str(context.ticket_dir(root, ticket) / GOAL_MD),
    }
