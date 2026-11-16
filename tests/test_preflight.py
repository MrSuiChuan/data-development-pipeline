"""Pre-development mode selection, ledger and goal gates."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
ENGINE = ROOT_REPO / "engine"
sys.path.insert(0, str(ENGINE))

from core import context  # noqa: E402


GOOD_DESC = (
    "## 背景\n"
    "现有汇总表缺少客单价，业务需要统一口径。\n\n"
    "## 目标\n"
    "在 dwd_order_province_di 增加 avg_amount，按省份 + 日期产出。\n\n"
    "## 范围\n"
    "目标表: dwd_order_province_di\n"
    "来源: ods_order_src\n"
    "只改这一张表，不改上游。\n\n"
    "## 验收口径\n"
    "avg_amount 与源表 sum(amount) / count(order_id) 全量比对差异为 0。\n\n"
    "## 风险\n"
    "三张下游报表需要回归。\n\n"
    "负责人: 张三\n"
    "计划日期: 2026-10-30\n"
)


def run_engine(root: Path, *argv: str, check: bool = False) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ENGINE / "ddp.py"), "--root", str(root), *argv]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError("engine %s failed rc=%s\nSTDOUT:\n%s\nSTDERR:\n%s" % (
            argv, proc.returncode, proc.stdout, proc.stderr
        ))
    return proc


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        run_engine(self.root, "setup", check=True)
        run_engine(
            self.root,
            "ticket", "create",
            "--title", "【数据需求】客单价加列",
            "--description", GOOD_DESC,
            check=True,
        )
        self.tid = context.list_ticket_ids(self.root)[0]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _select(self, mode: str = "interactive") -> subprocess.CompletedProcess:
        return run_engine(
            self.root,
            "mode", "select",
            "--ticket", self.tid,
            "--mode", mode,
            "--chosen-by", "测试用户",
        )

    def test_start_requires_mode_choice(self) -> None:
        proc = run_engine(self.root, "start", "--ticket", self.tid)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mode_choice_required", proc.stdout)

    def test_start_requires_goal_after_mode(self) -> None:
        self._select("interactive")
        proc = run_engine(self.root, "start", "--ticket", self.tid)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("goal_required", proc.stdout)

    def test_mode_and_goal_are_recorded_in_ledger(self) -> None:
        self._select("autopilot")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)

        ledger = json.loads(
            (self.root / ".data-dev" / "runtime" / "preflight_ledger.json").read_text(encoding="utf-8")
        )
        kinds = [entry.get("kind") for entry in ledger.get("entries", [])]
        self.assertIn("mode", kinds)
        self.assertIn("goal", kinds)

        goal = json.loads(
            (self.root / ".data-dev" / "tickets" / self.tid / "_goal.json").read_text(encoding="utf-8")
        )
        self.assertEqual(goal["mode"], "autopilot")
        self.assertEqual(goal["target_table"], "dwd_order_province_di")
        self.assertIn("avg_amount", goal["objective"])
        self.assertIn("差异为 0", goal["acceptance"])
        self.assertTrue(
            (self.root / ".data-dev" / "tickets" / self.tid / "_goal.md").is_file()
        )

    def test_goal_created_before_sds_still_lets_start_reach_admission(self) -> None:
        self._select("interactive")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        proc = run_engine(self.root, "start", "--ticket", self.tid)
        self.assertNotEqual(proc.returncode, 0)
        # No SDS yet: the preflight passed and admission rejected at the SDS anchor.
        self.assertIn("start_admission", proc.stdout)
        self.assertNotIn("goal_required", proc.stdout)

    def test_mode_and_goal_survive_sds_fingerprint_change(self) -> None:
        """SDS bridge 会把 state.ticket_hash 改成语义指纹；预检绑定的是工单正文。"""
        self._select("interactive")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        state_path = self.root / ".data-dev" / "tickets" / self.tid / "_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["ticket_hash"] = "sds-fingerprint-after-bridge"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        proc = run_engine(self.root, "start", "--ticket", self.tid)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("mode_stale", proc.stdout)
        self.assertNotIn("goal_stale", proc.stdout)
        self.assertIn("start_admission", proc.stdout)

    def test_invalid_mode_is_rejected(self) -> None:
        proc = self._select("auto")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mode_choice", proc.stdout)

    def test_autopilot_flag_must_match_ledger(self) -> None:
        self._select("interactive")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        proc = run_engine(self.root, "start", "--ticket", self.tid, "--autopilot")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mode_mismatch", proc.stdout)

    def test_mode_is_locked_after_development_starts(self) -> None:
        self._select("interactive")
        state_path = self.root / ".data-dev" / "tickets" / self.tid / "_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["current_phase"] = 2
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        proc = self._select("autopilot")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mode_locked", proc.stdout)
        goal = run_engine(self.root, "goal", "create", "--ticket", self.tid, "--replace")
        self.assertNotEqual(goal.returncode, 0)
        self.assertIn("goal_locked", goal.stdout)

    def test_stale_goal_requires_replace(self) -> None:
        self._select("interactive")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        goal_path = self.root / ".data-dev" / "tickets" / self.tid / "_goal.json"
        goal = json.loads(goal_path.read_text(encoding="utf-8"))
        goal["ticket_hash"] = "stale"
        goal_path.write_text(json.dumps(goal, ensure_ascii=False, indent=2), encoding="utf-8")

        stale = run_engine(self.root, "goal", "create", "--ticket", self.tid)
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("goal_stale", stale.stdout)

        replaced = run_engine(self.root, "goal", "create", "--ticket", self.tid, "--replace", check=True)
        self.assertIn("G-", replaced.stdout)

    def test_stale_mode_blocks_start(self) -> None:
        self._select("interactive")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        mode_path = self.root / ".data-dev" / "tickets" / self.tid / "_mode.json"
        mode = json.loads(mode_path.read_text(encoding="utf-8"))
        mode["ticket_hash"] = "stale"
        mode_path.write_text(json.dumps(mode, ensure_ascii=False, indent=2), encoding="utf-8")
        proc = run_engine(self.root, "start", "--ticket", self.tid)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mode_stale", proc.stdout)


class PreflightHardeningTests(PreflightTests):
    """台账与快照被写坏、字段类型不对时：报可读的门，不静默 reset、不抛 traceback。"""

    def _ledger_path(self) -> Path:
        return self.root / ".data-dev" / "runtime" / "preflight_ledger.json"

    def _state_path(self) -> Path:
        return self.root / ".data-dev" / "tickets" / self.tid / "_state.json"

    def _patch_state(self, **fields: object) -> None:
        path = self._state_path()
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(fields)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_corrupt_ledger_is_refused_and_left_untouched(self) -> None:
        ledger = self._ledger_path()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"schema_version": 1, "entries": "boom"}', encoding="utf-8")
        before = ledger.read_bytes()

        proc = self._select("interactive")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ledger_corrupt", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(ledger.read_bytes(), before, "损坏的台账不允许被静默重置")

    def test_bad_next_entry_counter_is_refused(self) -> None:
        ledger = self._ledger_path()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"entries": [], "next_entry": "abc"}', encoding="utf-8")
        proc = self._select("interactive")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ledger_corrupt", proc.stdout)

    def test_non_numeric_phase_reports_gate_instead_of_crashing(self) -> None:
        self._patch_state(current_phase="P2")
        proc = self._select("interactive")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ticket_state_invalid", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_out_of_range_phase_reports_gate(self) -> None:
        self._patch_state(current_phase=99)
        proc = self._select("interactive")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ticket_state_invalid", proc.stdout)

    def test_mode_snapshot_without_hash_is_treated_as_stale(self) -> None:
        self._select("interactive")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        mode_path = self.root / ".data-dev" / "tickets" / self.tid / "_mode.json"
        mode = json.loads(mode_path.read_text(encoding="utf-8"))
        mode.pop("ticket_hash", None)
        mode_path.write_text(json.dumps(mode, ensure_ascii=False, indent=2), encoding="utf-8")

        proc = run_engine(self.root, "start", "--ticket", self.tid)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mode_stale", proc.stdout)

    def test_non_object_goal_snapshot_is_replaced(self) -> None:
        self._select("interactive")
        goal_path = self.root / ".data-dev" / "tickets" / self.tid / "_goal.json"
        goal_path.write_text("[]", encoding="utf-8")

        proc = run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        self.assertIn("G-", proc.stdout)

    def test_summary_is_not_ready_without_ticket_hash(self) -> None:
        from core import preflight

        self._select("interactive")
        run_engine(self.root, "goal", "create", "--ticket", self.tid, check=True)
        req = context.ticket_dir(self.root, self.tid)
        for name in ("_mode.json", "_goal.json"):
            path = req / name
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("ticket_hash", None)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        payload = preflight.summary(self.root, self.tid)
        self.assertFalse(payload["ready"], "缺 hash 时不能判 ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
