"""End-to-end tests: demo happy path, hook smoke, engine admission rules."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
ENGINE = ROOT_REPO / "engine"
sys.path.insert(0, str(ENGINE))

from core import context  # noqa: E402


def run_engine(root: Path, *argv: str, check: bool = False) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ENGINE / "ddp.py"), "--root", str(root), *argv]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError(f"engine {argv} failed rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return proc


class DemoPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self, ticket_id: str) -> dict:
        path = self.root / ".data-dev" / "tickets" / ticket_id / "_state.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_demo_autopilot_full_loop(self) -> None:
        proc = run_engine(self.root, "demo", "--autopilot")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        state = self._state("T-0001")
        self.assertTrue(state["finished"])
        self.assertEqual(state["current_phase"], 7)
        self.assertEqual(state["run_mode"], "autopilot")
        self.assertIn(6, state["completed"])

    def test_demo_interactive_full_loop(self) -> None:
        proc = run_engine(self.root, "demo")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        state = self._state("T-0001")
        self.assertTrue(state["finished"])
        self.assertEqual(state["run_mode"], "interactive")

    def test_submit_quality_gate_on_cli(self) -> None:
        run_engine(self.root, "setup", check=True)
        bad = run_engine(
            self.root,
            "ticket", "create",
            "--title", "坏工单",
            "--description", "没有五章",
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("submit_quality_gate", bad.stdout)
        good_desc = (
            "## 背景\n背景内容。\n\n## 目标\n目标内容。\n\n## 范围\n目标表: t\n来源: s\n\n"
            "## 验收口径\n验收: 指标一致。\n\n## 风险\n风险：下游。\n\n负责人: 张三\n计划日期: 2026-09-30\n"
        )
        good = run_engine(self.root, "ticket", "create", "--title", "【数据需求】好工单", "--description", good_desc)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

    def test_advance_after_finish_blocked(self) -> None:
        run_engine(self.root, "demo", "--autopilot", check=True)
        proc = run_engine(self.root, "advance", "--ticket", "T-0001", "--phase", "3", "--reason", "已闭环还想改")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("已完成", proc.stdout)


class HookSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        self.env = dict(os.environ)
        self.env["CLAUDE_PLUGIN_ROOT"] = str(ROOT_REPO)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _hook(self, name: str, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT_REPO / "hooks" / name)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            cwd=str(self.root),
        )

    def test_pretooluse_blocks_bad_publish(self) -> None:
        proc = self._hook(
            "pretooluse.py",
            {
                "hook_event_name": "PreToolUse",
                "cwd": str(self.root),
                "tool_name": "Bash",
                "tool_input": {"command": "platform publish --name x --comment 没token", "description": ""},
            },
        )
        # 宿主 schema 是 additionalProperties:false：用 exit 0 + JSON 决策表达阻断
        # （exit 2 只在把原因写 stderr 的旧协议里有效，且多余字段会让宿主整条忽略决策）
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload.get("decision"), "block")
        specific = payload["hookSpecificOutput"]
        self.assertEqual(specific.get("permissionDecision"), "deny")
        self.assertTrue(specific.get("permissionDecisionReason"))

    def test_hook_output_uses_host_schema_keys_only(self) -> None:
        """Codex/Claude Code 的 hook 输出都不接受额外字段，多余键会导致决策被忽略。"""
        top_allowed = {
            "continue",
            "decision",
            "hookSpecificOutput",
            "reason",
            "stopReason",
            "suppressOutput",
            "systemMessage",
        }
        specific_allowed = {
            "hookEventName",
            "permissionDecision",
            "permissionDecisionReason",
            "additionalContext",
        }
        proc = self._hook(
            "pretooluse.py",
            {
                "hook_event_name": "PreToolUse",
                "cwd": str(self.root),
                "tool_name": "Bash",
                "tool_input": {"command": "platform publish --name x --comment 没token", "description": ""},
            },
        )
        payload = json.loads(proc.stdout)
        self.assertLessEqual(set(payload), top_allowed, payload)
        self.assertLessEqual(set(payload.get("hookSpecificOutput", {})), specific_allowed, payload)

        post = self._hook(
            "posttooluse.py",
            {
                "hook_event_name": "PostToolUse",
                "cwd": str(self.root),
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.root / ".data-dev" / "tickets" / "T-0001" / "x.md"), "content": "x"},
            },
        )
        post_payload = json.loads(post.stdout)
        self.assertLessEqual(set(post_payload), top_allowed, post_payload)
        self.assertLessEqual(set(post_payload.get("hookSpecificOutput", {})), specific_allowed, post_payload)

    def test_pretooluse_allows_plain_echo(self) -> None:
        proc = self._hook(
            "pretooluse.py",
            {
                "hook_event_name": "PreToolUse",
                "cwd": str(self.root),
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello", "description": ""},
            },
        )
        self.assertEqual(proc.returncode, 0)

    def test_posttooluse_runs_and_syncs_context(self) -> None:
        # create a ticket workspace with a deploy_plan write and check soft sync
        from core.state import Pipeline

        req = context.ticket_dir(self.root, "T-0001")
        Pipeline.init(req, "T-0001", "t", "d", "h")
        payload = {
            "hook_event_name": "PostToolUse",
            "cwd": str(self.root),
            "tool_name": "Write",
            "tool_input": {"file_path": str(req / "deploy_plan.json"), "content": '{"bad": '},
        }
        proc = self._hook("posttooluse.py", payload)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
