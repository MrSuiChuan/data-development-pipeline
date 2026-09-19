"""Pipeline state machine for the data-development-plugin.

The machine is intentionally dumb: it only records machine-checkable facts and
lets ``validators.stage_gate`` decide whether a phase is complete enough to
leave.  The AI never reports its own progress directly -- every context key is
written by a validator / hook after checking artifacts on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import context

SDS_STATUS_NONE = "none"
SDS_IN_PROGRESS = "in_progress"
SDS_REVIEW_PENDING = "review_pending"  # knowledge_gap -> 复核门
SDS_REVIEW_BACKFILL = "review_backfill"  # 增量沉淀门
SDS_PENDING_CONFIRM = "pending_confirm"  # 确认门
SDS_PUBLISHED = "published"
SDS_BLOCKED = "blocked"

VALID_SDS_STATUS = {
    SDS_STATUS_NONE,
    SDS_IN_PROGRESS,
    SDS_REVIEW_PENDING,
    SDS_REVIEW_BACKFILL,
    SDS_PENDING_CONFIRM,
    SDS_PUBLISHED,
    SDS_BLOCKED,
}

LEVELS = ("L0", "L1", "L2", "L3")


def _is_blocking(problem: Any) -> bool:
    """是否应当阻断阶段转场。

    WARN 只提示不阻断（否则"新表无线上基准"这类提示会把整条流水线卡死）；
    FAIL / HALTED 阻断；未知结构按阻断处理，保持保守。
    """
    severity = getattr(problem, "severity", None)
    if severity is None and isinstance(problem, dict):
        severity = problem.get("severity")
    if severity is None:
        return True
    return str(severity).upper() in ("FAIL", "HALTED")


def default_state(ticket_id: str, title: str, description_md: str, ticket_hash: str) -> Dict[str, Any]:
    return {
        "schema_version": context.SCHEMA_VERSION,
        "ticket": ticket_id,
        "title": title or ticket_id,
        "description_md": description_md or "",
        "ticket_hash": ticket_hash or "",
        "current_phase": 0,
        "finished": False,
        "run_mode": "interactive",  # interactive | autopilot
        "level": None,
        "sds_design": {
            "status": SDS_STATUS_NONE,
            "mode": None,
            "confirmed": False,
            "ticket_hash": "",
            "kb_round": 0,
            "backfill_resolved": None,
            "final_version": 0,
            "published_at": None,
            "halted": False,
        },
        "completed": [],  # phase numbers whose exit gate has passed
        "awaiting_confirmation": False,
        "confirmation_scope": None,
        "autopilot_decisions": [],
        "transition_log": [],
        "context": {},  # machine-read keys consumed by PreToolUse gates
    }


class StateError(RuntimeError):
    pass


class Pipeline:
    """Thin wrapper around ``_state.json`` plus transition rules."""

    def __init__(self, directory: Path, ticket_id: Optional[str] = None):
        self.directory = Path(directory)
        self.state = context.load_json(context.state_path(self.directory))
        if not isinstance(self.state, dict):
            self.state = {}

    # ------------------------------------------------------------- lifecycle
    @classmethod
    def init(
        cls,
        directory: Path,
        ticket_id: str,
        title: str,
        description_md: str,
        ticket_hash: str = "",
    ) -> "Pipeline":
        context.ensure_dir(directory)
        state = default_state(ticket_id, title, description_md, ticket_hash)
        context.write_json(context.state_path(directory), state)
        pipeline = cls(directory)
        pipeline.refresh_context_md()
        return pipeline

    def save(self) -> None:
        self.state["ticket"] = self.state.get("ticket") or self.directory.name
        context.write_json(context.state_path(self.directory), self.state)
        self.refresh_context_md()

    def exists(self) -> bool:
        return bool(self.state)

    # ------------------------------------------------------------- helpers
    @property
    def ticket(self) -> str:
        return str(self.state.get("ticket") or self.directory.name)

    @property
    def phase(self) -> int:
        return int(self.state.get("current_phase", 0))

    @property
    def sds(self) -> Dict[str, Any]:
        sds = self.state.get("sds_design")
        if not isinstance(sds, dict):
            sds = {}
        return sds

    @property
    def level(self) -> Optional[str]:
        value = self.state.get("level")
        return value if value in LEVELS else None

    def phase_name(self, phase: Optional[int] = None) -> str:
        return context.PHASE_NAMES.get(self.phase if phase is None else phase, f"phase{phase}")

    def log_transition(self, kind: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        entry = {"kind": kind, "at": context.localnow(), "message": message}
        if extra:
            entry.update(extra)
        self.state.setdefault("transition_log", []).append(entry)

    def context_key(self, key: str, default: Any = None) -> Any:
        return self.state.setdefault("context", {}).get(key, default)

    def set_context(self, key: str, value: Any, log: bool = True) -> None:
        self.state.setdefault("context", {})[key] = value
        if log:
            self.log_transition("context", f"context.{key} = {value!r}")
        self.save()

    def require_context_keys(self, mapping: Dict[str, Any]) -> List[str]:
        missing = [k for k, expected in mapping.items() if self.context_key(k) != expected]
        return missing

    # ------------------------------------------------------------- SDS state
    def set_sds_status(self, status: str, reason: str = "") -> None:
        if status not in VALID_SDS_STATUS:
            raise StateError(f"非法 SDS 状态: {status}")
        self.sds["status"] = status
        if reason:
            self.log_transition("sds_status", f"SDS -> {status}: {reason}")
        self.save()

    def enter_sds(self, mode: str) -> None:
        if mode not in ("verify", "generate"):
            raise StateError("SDS mode 只能是 verify 或 generate")
        self.sds["mode"] = mode
        self.sds["status"] = SDS_IN_PROGRESS
        self.sds["halted"] = False
        if self.phase < 1:
            self.state["current_phase"] = 1
        self.log_transition("sds", f"进入 SDS 设计（mode={mode}）")
        self.save()

    def sds_halt(self, reason: str) -> None:
        self.sds["status"] = SDS_BLOCKED
        self.sds["halted"] = True
        self.log_transition("sds_halt", f"SDS 证据比对不一致，转人工核查: {reason}")
        self.save()

    def bump_kb_round(self) -> int:
        rounds = int(self.sds.get("kb_round", 0)) + 1
        self.sds["kb_round"] = rounds
        self.save()
        return rounds

    def resolve_backfill(self, resolution: str, reason: str = "") -> None:
        if resolution not in ("synced", "declined"):
            raise StateError("backfill_resolved 只能是 synced 或 declined")
        self.sds["backfill_resolved"] = resolution
        self.log_transition("sds_backfill", f"增量沉淀决议={resolution}: {reason}")
        self.save()

    def mark_published(self, version: int) -> None:
        self.sds["status"] = SDS_PUBLISHED
        self.sds["confirmed"] = True
        self.sds["final_version"] = int(version)
        self.sds["published_at"] = context.localnow()
        self.log_transition("sds_published", f"SDS 终版 v{version} 已贴单并回读校验通过")
        self.save()

    def set_level(self, level: str, reason: str = "") -> None:
        if level not in LEVELS:
            raise StateError(f"难度等级必须是 {LEVELS}")
        self.state["level"] = level
        self.set_context("level", level, log=False)
        if reason:
            self.log_transition("level", f"难度定级 {level}: {reason}")
        self.save()

    # ---------------------------------------------------------- run mode / gates
    def set_run_mode(self, mode: str) -> None:
        if mode not in ("interactive", "autopilot"):
            raise StateError("run_mode 只能是 interactive 或 autopilot")
        self.state["run_mode"] = mode
        self.log_transition("run_mode", f"运行模式 -> {mode}")
        self.save()

    def enter_confirmation(self, scope: str) -> None:
        self.state["awaiting_confirmation"] = True
        self.state["confirmation_scope"] = scope
        self.log_transition("confirmation", f"进入确认门（scope={scope}）：所有交付物写入/生产CLI将被锁")
        self.save()

    def leave_confirmation(self) -> None:
        self.state["awaiting_confirmation"] = False
        self.state["confirmation_scope"] = None
        self.log_transition("confirmation", "确认完成，解除写锁")
        self.save()

    def decide(self, title: str, entry: str, risk: bool = False) -> None:
        """Record an autopilot / interactive decision into autopilot_decisions.md."""
        record = {
            "at": context.localnow(),
            "title": title,
            "risk": bool(risk),
            "entry": entry,
        }
        self.state.setdefault("autopilot_decisions", []).append(record)
        self.save()
        lines = [
            "# autopilot_decisions.md",
            "",
            "> 本文件由 data-development-plugin 记录。⚠️ 行=不可逆风险决策（部署门/语义层进入门），autopilot 不旁路，部署前必须全文展示。",
            "",
        ]
        for item in self.state["autopilot_decisions"]:
            tag = "⚠️ " if item.get("risk") else "    "
            lines.append(f"- {tag}[{item['at']}] **{item['title']}**\n\n  {item['entry']}")
        context.write_text(self.directory / "autopilot_decisions.md", "\n".join(lines))

    # ---------------------------------------------------------- phase transitions
    def enter_dev_from_sds(self, run_mode: str) -> None:
        """start 命令专属转场：SDS 已 published -> 研发态 P2。"""
        if self.sds.get("status") != SDS_PUBLISHED:
            raise StateError("SDS 未 published，不允许进入研发态")
        if not self.sds.get("confirmed"):
            raise StateError("SDS 未 confirmed，不允许进入研发态")
        self.state["run_mode"] = run_mode if run_mode in ("interactive", "autopilot") else "interactive"
        if 1 not in self.state.get("completed", []):
            self.state.setdefault("completed", []).append(1)
        self.state["current_phase"] = 2
        self.log_transition("start", f"start 准入门通过，进入研发态 P2（run_mode={self.state['run_mode']}）")
        self.save()

    def advance(
        self,
        target: int,
        reason: str,
        allow_back: bool = False,
        gate: Optional[Callable[[int], List[Any]]] = None,
    ) -> Dict[str, Any]:
        """Move to ``target``.

        - forward (target == current + 1): optional ``gate(current)`` must pass
          (gate returns [] when the exit criteria are met);
        - backward (target < current): only with ``allow_back`` and a reason;
          never re-appends to ``completed`` and never re-runs forward gates.
        """
        current = self.phase
        target = int(target)
        if not reason:
            return {"ok": False, "blocked": True, "reason": "阶段转场必须带 --reason，说明依据。"}
        if self.state.get("finished"):
            return {"ok": False, "blocked": True, "reason": "流水线已完成，禁止再转场。"}
        if target < 1 or target > 7:
            return {"ok": False, "blocked": True, "reason": f"目标阶段越界: {target}（允许 1..7）。"}

        if target == current + 1:
            problems = gate(current) if gate else []
            blocking = [p for p in problems if _is_blocking(p)]
            if blocking:
                detail = "; ".join(str(p) for p in blocking[:8])
                return {
                    "ok": False,
                    "blocked": True,
                    "gate": f"stage_gate:P{current}",
                    "reason": f"P{current} 出口门控未通过，禁止推进到 P{target}。{detail}",
                    "problems": [str(p) for p in blocking],
                }
            if current not in self.state.get("completed", []):
                self.state.setdefault("completed", []).append(current)
            self.state["current_phase"] = target
            self.log_transition("advance", f"前向推进 P{current} -> P{target}: {reason}")
            self.save()
            return {"ok": True, "phase": target, "reason": reason}

        if target < current:
            if not allow_back:
                return {
                    "ok": False,
                    "blocked": True,
                    "gate": "back_jump",
                    "reason": f"回跳 P{current} -> P{target} 必须带 --allow-back --reason（跳过前向门控，不追加 completed）。",
                }
            self.state["current_phase"] = target
            self.log_transition("back", f"回跳 P{current} -> P{target}（allow-back）: {reason}")
            self.save()
            return {"ok": True, "phase": target, "back": True, "reason": reason}

        if target == current:
            return {"ok": True, "phase": current, "noop": True, "reason": reason}

        return {
            "ok": False,
            "blocked": True,
            "reason": f"禁止跳转（P{current} -> P{target}）：只允许逐级前向推进或 --allow-back 回跳。",
        }

    def finish(self, reason: str) -> None:
        if self.phase == 6 and 6 not in self.state.get("completed", []):
            self.state.setdefault("completed", []).append(6)
        self.state["current_phase"] = 7
        self.state["finished"] = True
        self.log_transition("finish", f"流水线闭环: {reason}")
        self.save()

    # ------------------------------------------------------------- _context.md
    def refresh_context_md(self) -> None:
        lines = [
            "# _context.md — data-development-plugin 状态镜像（机器写入，勿手改）",
            "",
            f"ticket={self.ticket}",
            f"current_phase={self.phase}",
            f"phase_name={self.phase_name()}",
            f"finished={self.state.get('finished', False)}",
            f"run_mode={self.state.get('run_mode', 'interactive')}",
            f"level={self.level}",
            f"sds_status={self.sds.get('status')}",
            f"sds_confirmed={self.sds.get('confirmed', False)}",
            f"sds_backfill_resolved={self.sds.get('backfill_resolved')}",
            f"awaiting_confirmation={self.state.get('awaiting_confirmation', False)}",
            f"completed={','.join(str(p) for p in self.state.get('completed', []))}",
            "",
            "## context 键（PreToolUse 硬门消费）",
            "",
        ]
        for key in sorted(self.state.setdefault("context", {}).keys()):
            lines.append(f"{key}={self.state['context'][key]}")
        lines.append("")
        context.write_text(context.context_md_path(self.directory), "\n".join(lines))

    # ------------------------------------------------------------- summary
    def summary(self) -> Dict[str, Any]:
        return {
            "ticket": self.ticket,
            "phase": self.phase,
            "phase_name": self.phase_name(),
            "finished": self.state.get("finished", False),
            "run_mode": self.state.get("run_mode"),
            "level": self.level,
            "sds_status": self.sds.get("status"),
            "sds_confirmed": self.sds.get("confirmed", False),
            "awaiting_confirmation": self.state.get("awaiting_confirmation", False),
            "completed": self.state.get("completed", []),
            "context": dict(self.state.get("context", {})),
        }
