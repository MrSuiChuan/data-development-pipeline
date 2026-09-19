"""Run the configured hook command for real, through this platform's shell.

Unit tests call `gates.decide_*` directly, which proves the decision logic but
not the wiring. The failure this catches: a hooks file whose launcher does not
exist on this machine (`py -3` on macOS/Linux) — the host then fails open and
every hard gate silently stops enforcing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
HOOKS_FILE = ROOT_REPO / "hooks" / "claude-codex-hooks.json"


def pretooluse_command() -> str:
    data = json.loads(HOOKS_FILE.read_text(encoding="utf-8"))
    command = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    return command.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT_REPO))


class HookWiringTests(unittest.TestCase):
    """这些用例故意慢：每次都要真起一个 hook 进程。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # 没有 .data-dev/ 的干净目录：setup_guard 的触发条件
        self.workdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_hook(self, payload: dict) -> dict:
        command = pretooluse_command()
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(ROOT_REPO)
        proc = subprocess.run(
            command,
            shell=True,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.workdir),
            env=env,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"hook 命令没能正常跑完（疑似启动器在本机不存在）: {command}\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}",
        )
        body = (proc.stdout or "").strip()
        self.assertTrue(body, f"hook 没有输出决策（启动器或导入失败）: stderr={proc.stderr!r}")
        return json.loads(body.splitlines()[-1])

    def _payload(self, tool_name: str, tool_input: dict) -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.workdir),
            "tool_name": tool_name,
            "tool_input": tool_input,
        }

    def test_bare_cli_before_setup_is_denied(self) -> None:
        out = self._run_hook(self._payload("Bash", {"command": "platform runSql --sql 'select 1'"}))
        self.assertEqual(out.get("decision"), "block", out)
        self.assertEqual(out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny", out)

    def test_readonly_mention_of_cli_name_is_allowed(self) -> None:
        """实测误伤回归：只读检索里出现 platform/quality 不该被拦。"""
        for command in (
            'rg -n "platform" engine/core/sql_platforms.py',
            'rg -n -i "dqc|quality" engine/core/validators.py',
        ):
            out = self._run_hook(self._payload("Bash", {"command": command}))
            self.assertNotEqual(out.get("decision"), "block", f"{command} -> {out}")

    def test_ungated_tool_takes_fast_path(self) -> None:
        out = self._run_hook(self._payload("Read", {"file_path": "README.md"}))
        self.assertEqual(out, {}, out)


if __name__ == "__main__":
    unittest.main()
