"""Unit tests for hard gates, validators and the state machine (no Claude Code needed)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
ENGINE = ROOT_REPO / "engine"
sys.path.insert(0, str(ENGINE))

from core import clients, context, gates, state, validators  # noqa: E402
from core.state import Pipeline  # noqa: E402


class GateTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        self.title = "【数据需求】测试工单"
        self.description = (
            "## 背景\n需要收敛活跃口径。\n\n## 目标\n产出 ads 表。\n\n"
            "## 范围\n目标表: ads_user_daily_t\n来源: dwd_user_t\n\n"
            "## 验收口径\n验收: join>=0.995 null=0\n\n## 风险\n下游影响\n\n"
            "负责人: 张三\n计划日期: 2026-09-30\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_pipeline(self, ticket_id: str = "T-0001", phase: int = 3, run_mode: str = "interactive") -> Pipeline:
        req = context.ticket_dir(self.root, ticket_id)
        pipeline = Pipeline.init(req, ticket_id, self.title, self.description, context.sha256_text(self.description))
        pipeline.state["current_phase"] = phase
        pipeline.set_run_mode(run_mode)
        return pipeline


class SubmitQualityTests(GateTestBase):
    def test_bad_description_blocked(self) -> None:
        issues = validators.validate_submit_quality("没有前缀", "太短")
        self.assertTrue(validators.has_blockers(issues))
        self.assertTrue(any(i.code == "submit.title.topic" for i in issues))

    def test_good_description_passes(self) -> None:
        issues = validators.validate_submit_quality(self.title, self.description)
        self.assertFalse(validators.has_blockers(issues), issues)


class SdsValidatorTests(GateTestBase):
    def _sds(self, ticket_id: str = "T-0001", uri: str = "viking://resources/domain/dwd-user/readme.md") -> dict:
        return {
            "ticket_id": ticket_id,
            "title": self.title,
            "mode": "verify",
            "declared_source": "dwd_user_di",
            "level_suggestion": "L1",
            "target_table": {"name": "ads_user_act_di", "layer": "ads", "domain": "user", "comment": "c"},
            "field_mappings": [
                {
                    "target_field": "user_id",
                    "source_table": "dwd_user_di",
                    "source_field": "user_id",
                    "source_layer": "dwd",
                    "conclusion": "accurate",
                    "correction": "",
                    "sql_evidence": {"sql_id": "ev1", "join_match_rate": 0.9991, "null_rate": 0.0, "row_count": 100, "key_kind": "pk"},
                }
            ],
            "needs_intermediate_table": False,
            "knowledge_source_uri": uri,
            "knowledge_gaps": [],
            "knowledge_backfill": {"required": False, "resolved": None},
            "sql_validation": {"sql_count": 1, "join_match_rate": 0.9991, "null_rate": 0.0, "row_count": 100},
            "confirmed": True,
        }

    def _write(self, req: Path, sds: dict) -> None:
        context.write_json(req / "sds_design.json", sds)
        context.ensure_dir(req / "verification")
        context.write_json(req / "verification" / "evidence.json", sds["sql_validation"])
        context.write_text(req / "verification" / "q1.sql", "SELECT 1;\n")

    def test_uri_scope_fails(self) -> None:
        req = context.ticket_dir(self.root, "T-0001")
        context.ensure_dir(req)
        self._write(req, self._sds(uri="https://evil.example/x"))
        issues = validators.validate_sds_design(req)
        self.assertTrue(any(i.code == "sds.traceability.scope" for i in issues))

    def test_evidence_mismatch_halts(self) -> None:
        req = context.ticket_dir(self.root, "T-0001")
        context.ensure_dir(req)
        sds = self._sds()
        self._write(req, sds)
        evidence = dict(sds["sql_validation"])
        evidence["row_count"] = 999
        context.write_json(req / "verification" / "evidence.json", evidence)
        issues = validators.validate_sds_design(req)
        halted = [i for i in issues if i.severity == "HALTED"]
        self.assertTrue(halted, issues)


class SqlComplianceTests(GateTestBase):
    def test_alter_in_dp_task_fails(self) -> None:
        sql = "-- table: ads_user_daily_t\n-- schedule: 0 2 * * *\nALTER TABLE ads_user_daily_t ADD COLUMN x STRING;\n"
        issues = validators.sql_compliance_check(sql, filename="ads_user_daily_t.sql", is_dp_task=True)
        self.assertTrue(any(i.code == "sql.compliance.alter_in_task" for i in issues))

    def test_delete_without_where_fails(self) -> None:
        issues = validators.sql_compliance_check("DELETE FROM ads_user_daily_t;", filename="t.sql")
        self.assertTrue(any(i.code == "sql.compliance.full_scan_write" for i in issues))

    def test_select_star_fails(self) -> None:
        sql = "-- table: ads_user_daily_t\n-- source: dwd_user_t\nINSERT OVERWRITE TABLE ads_user_daily_t SELECT * FROM dwd_user_t;\n"
        issues = validators.sql_compliance_check(sql, filename="ads_user_daily_t.sql")
        self.assertTrue(any(i.code == "sql.compliance.select_star" for i in issues))

    def test_readonly_test_sql_passes_without_target_annotation(self) -> None:
        """P4 用例是只读 SELECT，文件名 tc_0001.sql 不该被"目标表/文件名一致"规则判 FAIL。"""
        sql = "SELECT COUNT(*) AS diff_rows FROM (SELECT province FROM dwd_t GROUP BY province) a;\n"
        issues = validators.sql_compliance_check(sql, filename="tc_0001.sql")
        self.assertFalse(validators.has_blockers(issues), issues)
        self.assertTrue(any(i.code == "sql.compliance.readonly" for i in issues))

    def test_write_sql_still_requires_target(self) -> None:
        issues = validators.sql_compliance_check("INSERT INTO t SELECT 1;", filename="t.sql")
        self.assertTrue(any(i.code == "sql.compliance.target" for i in issues))

    def test_write_sql_name_mismatch_still_fails(self) -> None:
        sql = "-- table: dwd_order_province_di\nINSERT INTO dwd_order_province_di SELECT 1;\n"
        issues = validators.sql_compliance_check(sql, filename="something_else.sql")
        self.assertTrue(any(i.code == "sql.compliance.name_mismatch" for i in issues))


class StateMachineTests(GateTestBase):
    def test_advance_requires_reason(self) -> None:
        pipeline = self._make_pipeline(phase=2)
        result = pipeline.advance(3, reason="", gate=lambda ph: [])
        self.assertFalse(result["ok"])

    def test_advance_gate_fail_blocks(self) -> None:
        pipeline = self._make_pipeline(phase=2)
        result = pipeline.advance(3, reason="x", gate=lambda ph: [validators.Issue("x", 2, "FAIL", "缺交付物")])
        self.assertFalse(result["ok"])
        self.assertEqual(result["gate"], "stage_gate:P2")
        self.assertEqual(pipeline.phase, 2)

    def test_advance_forward_ok(self) -> None:
        pipeline = self._make_pipeline(phase=2)
        result = pipeline.advance(3, reason="ok", gate=lambda ph: [])
        self.assertTrue(result["ok"])
        self.assertEqual(pipeline.phase, 3)
        self.assertIn(2, pipeline.state["completed"])

    def test_back_jump_needs_allow_back(self) -> None:
        pipeline = self._make_pipeline(phase=3)
        result = pipeline.advance(2, reason="回改")
        self.assertFalse(result["ok"])
        result = pipeline.advance(2, reason="回改", allow_back=True)
        self.assertTrue(result["ok"])
        self.assertEqual(pipeline.phase, 2)
        self.assertNotIn(3, pipeline.state["completed"])

    def test_jump_forward_blocked(self) -> None:
        pipeline = self._make_pipeline(phase=2)
        result = pipeline.advance(5, reason="想跳")
        self.assertFalse(result["ok"])


class CliGateTests(GateTestBase):
    def test_write_verb_and_machine_asset_same_segment_blocked(self) -> None:
        """写动词与敏感路径同段 = 真写入，必须拦。"""
        self._make_pipeline(phase=3)
        for cmd in (
            'echo {} > .data-dev/runtime/x.json',
            'Set-Content .data-dev/_state.json -Value "{}"',
            'cd x && rm .data-dev/runtime/executions.json',
        ):
            decision = gates.decide_cli_command(cmd, self.root)
            self.assertTrue(decision.blocked(), (cmd, decision))

    def test_write_verb_and_machine_asset_in_different_segments_allowed(self) -> None:
        """跨行/跨段出现（如分析脚本里一行 shutil、另一行提到 _state.json）不该误伤。"""
        self._make_pipeline(phase=3)
        cmd = (
            'py -3 -c "\n'
            "import shutil\n"
            "print('check .data-dev/_state.json layout')\n"
            '"\n'
        )
        decision = gates.decide_cli_command(cmd, self.root)
        self.assertFalse(decision.blocked(), decision)

    def test_variable_indirection_still_blocked(self) -> None:
        """实测绕过：路径先在变量里赋值，写命令里只出现变量名。"""
        self._make_pipeline(phase=3)
        cmd = (
            "$target = Join-Path $PWD '.data-dev\\runtime\\x.json'\n"
            "Set-Content -Path $target -Value '{}'\n"
        )
        decision = gates.decide_cli_command(cmd, self.root)
        self.assertTrue(decision.blocked(), decision)

    def test_cd_into_sensitive_dir_then_relative_write_blocked(self) -> None:
        self._make_pipeline(phase=3)
        cmd = "cd .data-dev/runtime\nSet-Content final-check.json -Value '{}'"
        decision = gates.decide_cli_command(cmd, self.root)
        self.assertTrue(decision.blocked(), decision)

    def test_setup_guard_blocks_bare_cli(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        decision = gates.decide_cli_command("platform runSql --sql 'select 1'", empty)
        self.assertEqual(decision.gate, "setup_guard")
        self.assertTrue(decision.blocked())

    def test_setup_guard_blocks_cli_invocation_shapes(self) -> None:
        """段首是命令词的各种写法都算真调用。"""
        empty = self.root / "empty"
        empty.mkdir()
        for cmd in (
            "quality deploy --name ads_t",
            "./platform run-sql --file etl/ads_t.sql",
            '& "C:\\tools\\quality.exe" deploy',
            "cd /tmp && tracker query T-0001",
            "FOO=1 sudo tracker query T-0001",
            "echo warmup || platform publish --name ads_t",
        ):
            decision = gates.decide_cli_command(cmd, empty)
            self.assertEqual(decision.gate, "setup_guard", cmd)
            self.assertTrue(decision.blocked(), cmd)

    def test_setup_guard_allows_readonly_mentions(self) -> None:
        """实测误伤回归：只读分析里出现这些词，不该被当成裸调平台 CLI。"""
        empty = self.root / "empty"
        empty.mkdir()
        for cmd in (
            'rg -n "platform" engine/core/sql_platforms.py',
            'rg -n -i "dqc|quality|质量" engine/core/validators.py',
            'rg "backfill_runs|platform_tasks" engine --glob "!**/__pycache__/**"',
            "git log --grep=quality",
            "python -m pytest tests/test_quality.py",
            "Get-Content docs/platform-notes.md",
        ):
            decision = gates.decide_cli_command(cmd, empty)
            self.assertFalse(decision.blocked(), f"{cmd} -> {decision.gate}: {decision.reason}")

    def test_shell_write_to_runtime_blocked(self) -> None:
        """Codex 实测绕过：PowerShell New-Item + Set-Content 直写 .data-dev/runtime。"""
        self._make_pipeline(phase=3)
        cmd = (
            'New-Item -ItemType Directory -Force -Path ".data-dev\\runtime" | Out-Null; '
            'Set-Content -Path ".data-dev\\runtime\\x.json" -Value "{}" -NoEncoding'
        )
        decision = gates.decide_cli_command(cmd, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "runtime_guard")

    def test_shell_redirect_to_engine_blocked(self) -> None:
        self._make_pipeline(phase=3)
        decision = gates.decide_cli_command('echo x > .data-dev/engine/ddp.py', self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "engine_readonly")

    def test_shell_read_of_runtime_allowed(self) -> None:
        self._make_pipeline(phase=3)
        for cmd in ("Get-Content .data-dev/runtime/executions.json", "Test-Path .data-dev/runtime"):
            decision = gates.decide_cli_command(cmd, self.root)
            self.assertFalse(decision.blocked(), (cmd, decision))

    def test_shell_write_sql_outside_workspace_blocked(self) -> None:
        self._make_pipeline(phase=3)
        decision = gates.decide_cli_command('Set-Content -Path "etl/x.sql" -Value "SELECT 1;"', self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "workspace_guard")

    def test_publish_missing_token_blocked(self) -> None:
        pipeline = self._make_pipeline(phase=5)
        pipeline.set_context("verify_result", "passed")
        pipeline.set_context("self_check_result", "passed")
        pipeline.set_context("user_confirmed", True)
        decision = gates.decide_cli_command(
            "platform publish --name ads_user_daily_t --ticket T-0001 --comment 直接发",
            self.root,
        )
        self.assertEqual(decision.gate, "publish_gate")
        self.assertTrue(decision.blocked())

    def test_publish_with_token_and_evidence_allowed(self) -> None:
        pipeline = self._make_pipeline(phase=5, run_mode="autopilot")
        for key, value in (("verify_result", "passed"), ("self_check_result", "passed"), ("user_confirmed", True), ("code_diff.required", True)):
            pipeline.set_context(key, value)
        req = pipeline.directory
        context.write_text(req / "code_diff.html", "<html>diff</html>")
        context.write_text(req / "review_report.html", "<html>review</html>")
        decision = gates.decide_cli_command(
            'platform publish --name ads_user_daily_t --ticket T-0001 --comment "[已确认发布] 用户确认"',
            self.root,
        )
        self.assertFalse(decision.blocked(), decision)

    def test_kb_scope_guard_blocks_foreign_uri(self) -> None:
        pipeline = self._make_pipeline(phase=1)
        pipeline.set_sds_status(state.SDS_IN_PROGRESS, "开始")
        decision = gates.decide_cli_command(
            "knowledge read --uri viking://projects/other/x.md --ticket T-0001",
            self.root,
        )
        self.assertEqual(decision.gate, "kb_scope_guard")
        self.assertTrue(decision.blocked())


class WriteGateTests(GateTestBase):
    def test_l1_create_table_blocked(self) -> None:
        pipeline = self._make_pipeline(phase=3)
        pipeline.set_level("L1", "x")
        path = pipeline.directory / "ddl" / "ads_new.sql"
        decision = gates.decide_file_write(str(path), "CREATE TABLE ads_new (a STRING);", self.root)
        self.assertEqual(decision.gate, "difficulty_guard")
        self.assertTrue(decision.blocked())

    def test_etl_before_ddl_blocked(self) -> None:
        pipeline = self._make_pipeline(phase=3)
        pipeline.set_level("L2", "x")
        path = pipeline.directory / "etl" / "ads_x.sql"
        decision = gates.decide_file_write(str(path), "-- table: ads_x\nINSERT ...", self.root)
        self.assertEqual(decision.gate, "dev_order_guard")

    def test_confirmation_locks_writes(self) -> None:
        pipeline = self._make_pipeline(phase=5)
        pipeline.enter_confirmation("发布确认")
        path = pipeline.directory / "delivery_summary.md"
        decision = gates.decide_file_write(str(path), "summary", self.root)
        self.assertEqual(decision.gate, "confirmation_gate_hook")
        state_path = context.state_path(pipeline.directory)
        decision_state = gates.decide_file_write(str(state_path), '{"ok": true}', self.root)
        self.assertEqual(decision_state.gate, "state_write_guard")
        self.assertTrue(decision_state.blocked())

    def test_deploy_plan_invalid_json_blocked(self) -> None:
        pipeline = self._make_pipeline(phase=5)
        path = pipeline.directory / "deploy_plan.json"
        decision = gates.decide_file_write(str(path), "{not json", self.root)
        self.assertEqual(decision.gate, "deploy_plan_check_hook")


class CodexHostPayloadTests(GateTestBase):
    """Codex 宿主用 apply_patch / exec_command 等工具名，门控必须照样生效。"""

    def test_windows_trailing_dot_and_space_on_protected_dir(self) -> None:
        """Windows 会把 `runtime.` / `runtime ` 当成 `runtime`，等价写法必须拦住。"""
        self._make_pipeline(phase=3)
        for rel in (".data-dev/runtime./x.json", ".data-dev/runtime /x.json",
                    ".data-dev/engine./ddp.py", ".data-dev/engine /ddp.py"):
            decision = gates.decide_file_write(rel, "{}", self.root)
            self.assertTrue(decision.blocked(), (rel, decision))

    def test_windows_trailing_dot_on_state_file(self) -> None:
        pipeline = self._make_pipeline(phase=3)
        for rel in (str(pipeline.directory / "_state.json."), str(pipeline.directory / "_state.json ")):
            decision = gates.decide_file_write(rel, "{}", self.root)
            self.assertTrue(decision.blocked(), (rel, decision))
            self.assertEqual(decision.gate, "state_write_guard")

    def test_shape_fallback_for_unknown_or_missing_tool_name(self) -> None:
        """工具名缺失/没见过时，只要载荷里有受保护路径的形状，也要拦。"""
        self._make_pipeline(phase=3)
        for payload in (
            {"tool_input": {"file_path": ".data-dev/runtime/x.json"}},
            {"tool_name": "SomeNewTool", "tool_input": {"path": ".data-dev/engine/ddp.py"}},
            {"tool_name": "Write", "tool_input": [".data-dev/runtime/x.json"]},
        ):
            decision = gates.decide_pre_tool(payload, self.root)
            self.assertTrue(decision.blocked(), (payload, decision))

    def test_apply_patch_move_to_protected_dir_blocked(self) -> None:
        """对抗测试实测绕过：Update + Move to 可以把文件搬进 .data-dev/runtime。"""
        self._make_pipeline(phase=3)
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": (
                    "*** Begin Patch\n"
                    "*** Update File: ok.txt\n"
                    "*** Move to: .data-dev/runtime/evil.json\n"
                    "*** End Patch"
                )
            },
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "runtime_guard")

    def test_apply_patch_move_protected_file_away_blocked(self) -> None:
        """把受保护文件搬走同样是改写机器资产（源路径也要检查）。"""
        self._make_pipeline(phase=3)
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": (
                    "*** Begin Patch\n"
                    "*** Update File: .data-dev/engine/ddp.py\n"
                    "*** Move to: stolen.py\n"
                    "*** End Patch"
                )
            },
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "engine_readonly")

    def test_sds_publish_not_blocked_by_submit_guard(self) -> None:
        """P1 终版贴单走的是 sds publish，不能要求研发期的三个证据键。"""
        self._make_pipeline(phase=1)
        decision = gates.decide_cli_command(
            "py -3 .data-dev/engine/ddp.py --root . sds publish --ticket T-0001 --version 1", self.root
        )
        self.assertFalse(decision.blocked(), decision)

    def test_flag_parsing_survives_command_chaining(self) -> None:
        """命令串联不得把后一条命令吞进 --ticket 取值，短参也不得匹配到 update-task 的 -t。"""
        chained = gates.analyze_command(
            "py -3 .data-dev/engine/ddp.py --root . sds validate --ticket T-0001; py -3 .data-dev/engine/ddp.py status"
        )
        self.assertEqual(chained["ticket"], "T-0001")
        short = gates.analyze_command("py -3 .data-dev/engine/ddp.py platform update-task --name ads_t")
        self.assertNotEqual(short["ticket"], "ask")

    def test_apply_patch_json_content_not_treated_as_patch_text(self) -> None:
        """Codex 实机缺陷：整段补丁文本被当成文件内容 → sds_design.json 被判非法 JSON。"""
        pipeline = self._make_pipeline(phase=1)
        target = pipeline.directory / "sds_design.json"
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": (
                    "*** Begin Patch\n"
                    f"*** Add File: {target}\n"
                    '+{"ticket_id": "T-0001", "knowledge_source_uri": "viking://resources/domain/trade/readme.md"}\n'
                    "*** End Patch"
                )
            },
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertFalse(decision.blocked(), decision)

    def test_apply_patch_invalid_json_still_blocked(self) -> None:
        pipeline = self._make_pipeline(phase=1)
        target = pipeline.directory / "sds_design.json"
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "tool_name": "apply_patch",
            "tool_input": {"command": f"*** Begin Patch\n*** Add File: {target}\n+{{not json\n*** End Patch"},
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "sds_json_valid")

    def test_verification_dir_phase_guard_on_windows_paths(self) -> None:
        """Windows 相对路径是 verification\\q1.sql，前缀判断必须归一化后才生效。"""
        pipeline = self._make_pipeline(phase=0)
        target = pipeline.directory / "verification" / "q1.sql"
        decision = gates.decide_file_write(str(target), "SELECT 1;\n", self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "phase_guard")

    def test_codex_captured_apply_patch_payload(self) -> None:
        """真实抓取的 Codex payload：补丁文本在 tool_input['command']，路径是相对路径。"""
        payload = {
            "session_id": "01a090d3-8465-7770-b811-f7998ef7198a",
            "hook_event_name": "PreToolUse",
            "cwd": str(self.root),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: .data-dev/runtime/probe3.json\n+{}\n*** End Patch"
            },
            "tool_use_id": "call_1",
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "runtime_guard")

    def test_codex_captured_bash_payload_allowed(self) -> None:
        """Codex 的命令类工具叫 Bash，读 tool_input['command']（只读命令应放行）。"""
        payload = {
            "cwd": str(self.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "Test-Path .data-dev\\runtime"},
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertFalse(decision.blocked(), decision)

    def test_codex_apply_patch_runtime_ledger_blocked(self) -> None:
        target = self.root / ".data-dev" / "runtime" / "executions.json"
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {"input": f"*** Begin Patch\n*** Add File: {target}\n+{{}}\n*** End Patch\n"},
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "runtime_guard")

    def test_codex_apply_patch_engine_copy_blocked(self) -> None:
        target = self.root / ".data-dev" / "engine" / "ddp.py"
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {"input": f"*** Begin Patch\n*** Update File: {target}\n+# x\n*** End Patch\n"},
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "engine_readonly")

    def test_codex_apply_patch_order_guard(self) -> None:
        pipeline = self._make_pipeline(phase=3)
        target = pipeline.directory / "dp_task" / "t.sql"
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {"input": f"*** Begin Patch\n*** Add File: {target}\n+INSERT INTO t SELECT 1;\n*** End Patch\n"},
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "dev_order_guard")

    def test_codex_exec_command_publish_gate(self) -> None:
        self._make_pipeline(phase=5)
        payload = {
            "tool_name": "exec_command",
            "tool_input": {"command": "mysql -uroot -P9030 -e \"publish task ads_t\""},
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)

    def test_codex_apply_patch_workspace_guard(self) -> None:
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {"input": f"*** Begin Patch\n*** Add File: {self.root / 'etl' / 'x.sql'}\n+SELECT 1;\n*** End Patch\n"},
        }
        decision = gates.decide_pre_tool(payload, self.root)
        self.assertTrue(decision.blocked(), decision)
        self.assertEqual(decision.gate, "workspace_guard")


if __name__ == "__main__":
    unittest.main(verbosity=2)
