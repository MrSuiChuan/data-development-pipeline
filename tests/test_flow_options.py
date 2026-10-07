"""Regression tests: environment-graded deploy, modeling/semantic switches, backdoor closing."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
ENGINE = ROOT_REPO / "engine"
sys.path.insert(0, str(ENGINE))

from core import clients, context, gates, validators  # noqa: E402
from core.state import Pipeline  # noqa: E402


def run_engine(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENGINE / "ddp.py"), "--root", str(root), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


class FlowOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        desc = (
            "## 背景\nx\n\n## 目标\nx\n\n## 范围\n目标表: ads_t\n来源: dwd_t\n\n"
            "## 验收口径\n验收: ok\n\n## 风险\nx\n\n负责人: a\n计划日期: 2026-09-30\n"
        )
        self.req = context.ticket_dir(self.root, "T-0001")
        self.pipeline = Pipeline.init(
            self.req, "T-0001", "【数据需求】选项测试", desc, context.sha256_text(desc)
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _set_config(self, patch: dict) -> None:
        cfg = context.load_config(self.root)
        for section, value in patch.items():
            cfg[section] = value
        context.write_json(context.config_path(self.root), cfg)

    def test_ctx_cannot_write_evidence_keys(self) -> None:
        proc = run_engine(self.root, "ctx", "set", "--ticket", "T-0001", "--key", "verify_result", "--value", "passed")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("state_write_guard", proc.stdout)
        self.assertIsNone(self.pipeline.context_key("verify_result"))

    def test_evidence_verify_requires_real_execution(self) -> None:
        fail = run_engine(self.root, "evidence", "verify", "--ticket", "T-0001")
        self.assertNotEqual(fail.returncode, 0)
        gates.mark_read(self.root, "ads_t")  # 写前已只读核对（backfill_guard 前置）
        run = run_engine(
            self.root,
            "platform", "run-sql",
            "--sql", "INSERT INTO TABLE ads_t SELECT 1 FROM dwd_t WHERE 1=1;",
            "--env", "dev",
            "--ticket", "T-0001",
            "--save-evidence",
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        ok = run_engine(self.root, "evidence", "verify", "--ticket", "T-0001")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(Pipeline(self.req).context_key("verify_result"), "passed")

    def test_fabricated_evidence_fails_ledger_check(self) -> None:
        context.write_json(
            self.req / "etl_dev_exec.json",
            {"status": "SUCCESS", "query_id": "q_fabricated", "env": "dev", "run_at": context.utcnow()},
        )
        proc = run_engine(self.root, "evidence", "verify", "--ticket", "T-0001")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("evidence_ledger", proc.stdout)

    def test_evidence_selfcheck_requires_diff_when_required(self) -> None:
        self.pipeline.set_context("code_diff.required", True)
        fail = run_engine(self.root, "evidence", "selfcheck", "--ticket", "T-0001")
        self.assertNotEqual(fail.returncode, 0)
        context.write_text(self.req / "code_diff.html", "<html>diff</html>")
        ok = run_engine(self.root, "evidence", "selfcheck", "--ticket", "T-0001")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

    def test_deploy_plan_accepts_dev_env(self) -> None:
        plan = {
            "ticket_id": "T-0001",
            "env": "dev",
            "publish_confirmed": False,
            "items": [{"action": "create", "table": "ads_t"}],
        }
        issues = validators.validate_deploy_plan_dict(plan, "T-0001")
        self.assertFalse(validators.has_blockers(issues), issues)

    def test_publish_gate_allows_nonprod_without_token(self) -> None:
        self.pipeline.state["current_phase"] = 5
        self.pipeline.set_context("verify_result", "passed")
        self.pipeline.set_context("self_check_result", "passed")
        self.pipeline.set_context("user_confirmed", True)
        context.write_json(
            self.req / "deploy_plan.json",
            {"ticket_id": "T-0001", "env": "dev", "items": [{"action": "create", "table": "ads_t"}]},
        )
        decision = gates.decide_cli_command(
            "platform publish --name ads_t --ticket T-0001 --comment ''", self.root
        )
        self.assertFalse(decision.blocked(), decision)

    def test_semantic_disabled_skips_phase6(self) -> None:
        self.pipeline.state["semantic"] = {"enabled": False}
        self.pipeline.save()
        problems = validators.stage_gate(6, self.req, self.pipeline.state)
        self.assertEqual(problems, [])

    def test_modeling_disabled_allows_unprefixed_l3_file(self) -> None:
        self._set_config({"modeling": {"layer_policy": "disabled"}})
        self.pipeline.state["current_phase"] = 3
        self.pipeline.set_level("L3", "x")
        path = self.req / "ddl" / "foo.sql"
        decision = gates.decide_file_write(str(path), "CREATE TABLE foo (a INT);", self.root)
        self.assertFalse(decision.blocked(), decision)

    def test_modeling_enabled_blocks_unprefixed_l3_file(self) -> None:
        self._set_config({"modeling": {"layer_policy": "enabled", "prefixes": ["ads", "dwd", "dim"]}})
        self.pipeline.state["current_phase"] = 3
        self.pipeline.set_level("L3", "x")
        path = self.req / "ddl" / "foo.sql"
        decision = gates.decide_file_write(str(path), "CREATE TABLE foo (a INT);", self.root)
        self.assertEqual(decision.gate, "difficulty_guard")
        self.assertTrue(decision.blocked())

    def test_etl_exec_evidence_write_gated_to_p3_p4(self) -> None:
        self.pipeline.state["current_phase"] = 2
        self.pipeline.save()
        decision = gates.decide_file_write(
            str(self.req / "etl_dev_exec.json"),
            '{"status":"SUCCESS","query_id":"x","env":"dev"}',
            self.root,
        )
        self.assertEqual(decision.gate, "phase_guard")
        self.assertTrue(decision.blocked())

    def test_ctx_cannot_toggle_code_diff_requirement(self) -> None:
        proc = run_engine(
            self.root, "ctx", "set", "--ticket", "T-0001", "--key", "code_diff.required", "--value", "false"
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("state_write_guard", proc.stdout)

    def test_stale_execution_evidence_rejected(self) -> None:
        import datetime as _dt

        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ok, reason = validators.execution_evidence_ok(
            {"status": "SUCCESS", "query_id": "q", "env": "dev", "executed_at": old},
            {"evidence_max_age_hours": 24},
        )
        self.assertFalse(ok)
        self.assertIn("过期", reason)

    def test_semantic_command_requires_user_confirmation_marker(self) -> None:
        fail = run_engine(self.root, "semantic", "--ticket", "T-0001")
        self.assertNotEqual(fail.returncode, 0)
        self.assertIn("semantic_entry_gate", fail.stdout)
        context.write_text(self.req / "_user_confirmation.md", "[已确认] 进入语义层（测试）")
        ok = run_engine(self.root, "semantic", "--ticket", "T-0001")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertTrue((self.req / "semantic_update_summary.md").is_file())

    def test_rollback_requires_plan_and_token(self) -> None:
        no_token = run_engine(self.root, "rollback", "--ticket", "T-0001", "--comment", "x")
        self.assertNotEqual(no_token.returncode, 0)
        self.assertIn("rollback_gate", no_token.stdout)
        context.write_json(
            self.req / "deploy_plan.json",
            {"ticket_id": "T-0001", "env": "dev", "items": [{"action": "create", "table": "ads_t"}]},
        )
        no_plan = run_engine(self.root, "rollback", "--ticket", "T-0001", "--comment", "[已回滚] x")
        self.assertNotEqual(no_plan.returncode, 0)
        self.assertIn("rollback_sql_file", no_plan.stdout)

    def test_runtime_ledger_files_are_protected(self) -> None:
        ledger = self.root / ".data-dev" / "runtime" / "executions.json"
        decision = gates.decide_file_write(str(ledger), "{}", self.root)
        self.assertEqual(decision.gate, "runtime_guard")
        self.assertTrue(decision.blocked())
        rm = gates.decide_cli_command(
            f"rm -f {self.root / '.data-dev' / 'runtime' / 'executions.json'}", self.root
        )
        self.assertEqual(rm.gate, "runtime_guard")
        self.assertTrue(rm.blocked())

    def test_engine_copy_write_blocked(self) -> None:
        decision = gates.decide_file_write(str(self.root / ".data-dev" / "engine" / "ddp.py"), "tampered", self.root)
        self.assertEqual(decision.gate, "engine_readonly")
        self.assertTrue(decision.blocked())

    def test_runtime_any_json_write_blocked(self) -> None:
        decision = gates.decide_file_write(
            str(self.root / ".data-dev" / "runtime" / "reads.json"), "{}", self.root
        )
        self.assertEqual(decision.gate, "runtime_guard")
        self.assertTrue(decision.blocked())

    def test_ticket_write_without_state_blocked(self) -> None:
        orphan = context.ticket_dir(self.root, "T-9999")
        context.ensure_dir(orphan)
        decision = gates.decide_file_write(str(orphan / "etl_dev_exec.json"), "{}", self.root)
        self.assertEqual(decision.gate, "ticket_not_initialized")
        self.assertTrue(decision.blocked())

    def test_bash_state_mutation_blocked(self) -> None:
        cmd = (
            "python -c \"import pathlib; "
            "pathlib.Path('.data-dev/tickets/T-0001/_state.json').unlink()\""
        )
        decision = gates.decide_cli_command(cmd, self.root)
        self.assertEqual(decision.gate, "state_write_guard")
        self.assertTrue(decision.blocked())

    def test_engine_integrity_blocks_on_mismatch(self) -> None:
        engine_dir = self.root / ".data-dev" / "engine"
        engine_dir.mkdir(parents=True)
        shutil.copy2(Path(__file__).resolve().parent.parent / "engine" / "core" / "context.py", engine_dir / "context.py")
        context.write_json(
            engine_dir / ".engine-manifest.json",
            {"schema_version": 1, "files": {"context.py": "deadbeef" * 8}},
        )
        proc = run_engine(self.root, "status", "--ticket", "T-0001")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("engine_integrity", proc.stdout)

    def test_engine_sync_repairs_project_copy(self) -> None:
        src = Path(__file__).resolve().parent.parent / "engine"
        proc = run_engine(self.root, "engine", "sync", "--from", str(src))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        engine_copy = self.root / ".data-dev" / "engine"
        self.assertTrue((engine_copy / "ddp.py").is_file())
        self.assertTrue((engine_copy / ".engine-manifest.json").is_file())
        status = run_engine(self.root, "status", "--ticket", "T-0001")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)

    def test_live_requires_execution_ledger_p3(self) -> None:
        context.write_json(
            self.req / "etl_dev_exec.json",
            {"status": "SUCCESS", "query_id": "q_live", "env": "dev", "executed_at": context.utcnow()},
        )
        issues = validators.validate_phase3_data_dev(self.req, {"mode": "live", "limits": {}})
        self.assertTrue(any(i.code == "dev.exec_evidence.no_ledger_store" for i in issues), issues)

    def test_live_requires_execution_ledger_p4(self) -> None:
        context.write_json(
            self.req / "test_runs.json",
            {"runs": [{"test_id": "TC-0001", "status": "SUCCESS", "query_id": "q_live"}]},
        )
        issues = validators.validate_phase4_testing(self.req, {"mode": "live", "level": "L1"})
        self.assertTrue(any(i.code == "test.evidence.no_ledger_store" for i in issues), issues)

    # ---- 证据必须绑定到它验证的那份 SQL（PIPE-001） ----

    ETL_V1 = (
        "-- source: dwd_t\n-- table: ads_t\n-- schedule: daily\n"
        "SELECT id, ds FROM dwd_t WHERE ds = '2026-09-19';\n"
    )
    ETL_V2 = (
        "-- source: dwd_t\n-- table: ads_t\n-- schedule: daily\n"
        "SELECT id, ds FROM dwd_t WHERE ds = '1970-01-01';\n"
    )

    def _p3_ready(self, etl: str | None = None) -> dict:
        """把工单铺成"P3 已真跑"的样子：三层目录一致 + 证据 + 执行账本。"""
        code = etl if etl is not None else self.ETL_V1
        for sub in ("ddl", "etl", "dp_task"):
            context.write_text(self.req / sub / "ads_t.sql", code)
        self._arch_doc(["ads_t"])
        context.write_json(
            self.req / "etl_dev_exec.json",
            {"status": "SUCCESS", "query_id": "q_real", "env": "dev", "executed_at": context.utcnow()},
        )
        context.write_json(
            context.ledger_dir(self.root) / "executions.json",
            {
                "runs": [
                    {
                        "query_id": "q_real",
                        "status": "SUCCESS",
                        "env": "dev",
                        "executed_at": context.utcnow(),
                        "sql_hash": context.sha256_text(self.ETL_V1),
                    }
                ]
            },
        )
        return {
            "mode": "live",
            "level": "L1",
            "limits": {},
            "context": {"self_check_result": "passed", "verify_result": "passed"},
        }

    def _arch_doc(self, tables: list) -> None:
        """写一份带交付表清单机读块的 architecture_doc.md。"""
        rows = ",\n".join('    {"name": "%s"}' % name for name in tables)
        context.write_text(
            self.req / "architecture_doc.md",
            "# architecture_doc.md\n\n难度级别: L1\n\n## 交付表清单\n\n"
            '```json\n{\n  "tables": [\n' + rows + "\n  ]\n}\n```\n\n"
            "## 链路\n\n```mermaid\ngraph LR\nA[dwd_t] --> B[ads_t]\n```\n",
        )

    def test_p3_evidence_matching_current_sql_passes(self) -> None:
        state = self._p3_ready()
        issues = validators.validate_phase3_data_dev(self.req, state)
        self.assertFalse(
            [i for i in issues if i.code == "evidence.binding.stale"], issues
        )

    def test_p3_blocks_when_etl_edited_after_evidence(self) -> None:
        state = self._p3_ready()
        # 过门之后把 ETL 换成另一个口径，账本里还是旧指纹
        for sub in ("etl", "dp_task"):
            context.write_text(self.req / sub / "ads_t.sql", self.ETL_V2)
        issues = validators.validate_phase3_data_dev(self.req, state)
        self.assertTrue(any(i.code == "evidence.binding.stale" for i in issues), issues)

    def test_p4_blocks_when_etl_edited_after_evidence(self) -> None:
        state = self._p3_ready()
        context.write_text(
            self.req / "test_report.md",
            "# 测试报告\n\nTC-0001 主键唯一 ✅ PASS (query_id: q_t1)\n" * 2,
        )
        context.write_json(
            self.req / "test_runs.json",
            {"runs": [{"test_id": "TC-0001", "status": "SUCCESS", "query_id": "q_t1"}]},
        )
        for sub in ("etl", "dp_task"):
            context.write_text(self.req / sub / "ads_t.sql", self.ETL_V2)
        issues = validators.validate_phase4_testing(self.req, state)
        self.assertTrue(any(i.code == "evidence.binding.stale" for i in issues), issues)

    def test_p4_blocks_evidence_past_its_freshness_window(self) -> None:
        """P3 之后搁置超过 evidence_max_age_hours，必须重新真跑。"""
        state = self._p3_ready()
        context.write_text(
            self.req / "test_report.md",
            "# 测试报告\n\nTC-0001 主键唯一 ✅ PASS (query_id: q_t1)\n" * 2,
        )
        context.write_json(
            self.req / "test_runs.json",
            {"runs": [{"test_id": "TC-0001", "status": "SUCCESS", "query_id": "q_t1"}]},
        )
        stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        context.write_json(
            self.req / "etl_dev_exec.json",
            {"status": "SUCCESS", "query_id": "q_real", "env": "dev", "executed_at": stale},
        )
        issues = validators.validate_phase4_testing(self.req, state)
        self.assertTrue(any(i.code == "test.precondition.etl" for i in issues), issues)
        self.assertTrue(
            any("过期" in i.message for i in issues if i.code == "test.precondition.etl"), issues
        )

    def test_p5_blocks_when_etl_edited_after_evidence(self) -> None:
        """P4 之后又改 ETL：不能部署一份没人验证过的代码。"""
        self._p3_ready()
        self._p5_ready([{"action": "create", "table": "ads_t"}])
        ok = validators.validate_phase5_deploy(self.req, {"mode": "mock"})
        self.assertFalse([i for i in ok if i.code == "evidence.binding.stale"], ok)
        for sub in ("etl", "dp_task"):
            context.write_text(self.req / sub / "ads_t.sql", self.ETL_V2)
        issues = validators.validate_phase5_deploy(self.req, {"mode": "mock"})
        self.assertTrue(any(i.code == "evidence.binding.stale" for i in issues), issues)

    # ---- 架构交付表清单 → 研发交付物的等值契约（PIPE-002） ----

    def test_p3_happy_path_has_no_failures(self) -> None:
        """设计一张、造一张，P3 出口应当全绿（在此之前架构对研发零约束）。"""
        state = self._p3_ready()
        issues = validators.validate_phase3_data_dev(self.req, state)
        self.assertFalse(validators.has_blockers(issues), validators.issues_to_dicts(issues))

    def test_p2_requires_delivery_table_block(self) -> None:
        context.write_text(self.req / "requirement_spec.md", "# spec\n有内容")
        context.write_text(self.req / "architecture_doc.md", "# doc\n\n难度级别: L1\n\n只说人话，没有机读块。\n")
        issues = validators.validate_phase2_architecture(self.req, "L1")
        self.assertTrue(any(i.code == "arch.tables.missing" for i in issues), issues)

    def test_p2_rejects_malformed_delivery_table_block(self) -> None:
        context.write_text(self.req / "requirement_spec.md", "# spec\n有内容")
        for body in (
            '```json\n{"tables": []}\n```\n',          # 空清单
            '```json\n{"tables": [{"action": "x"}]}\n```\n',   # 缺 name
            '```json\n{"tables": [{"name": "a"}, {"name": "a"}]}\n```\n',  # 重复
            "```json\n{不是合法 JSON}\n```\n",
        ):
            context.write_text(
                self.req / "architecture_doc.md",
                "# doc\n\n难度级别: L1\n\n" + body,
            )
            issues = validators.validate_phase2_architecture(self.req, "L1")
            self.assertTrue(any(i.code == "arch.tables.missing" for i in issues), (body, issues))

    def test_p3_blocks_declared_table_not_built(self) -> None:
        state = self._p3_ready()
        self._arch_doc(["ads_t", "dwd_mid_t"])   # 设计了两张，只造了一张
        issues = validators.validate_phase3_data_dev(self.req, state)
        self.assertTrue(any(i.code == "dev.arch.table_missing" for i in issues), issues)

    def test_p3_blocks_built_table_not_declared(self) -> None:
        state = self._p3_ready()
        for sub in ("ddl", "etl", "dp_task"):
            context.write_text(self.req / sub / "extra_t.sql", self.ETL_V1)
        issues = validators.validate_phase3_data_dev(self.req, state)
        self.assertTrue(any(i.code == "dev.arch.table_extra" for i in issues), issues)

    def test_p3_blocks_sds_target_table_not_in_delivery_tables(self) -> None:
        state = self._p3_ready()
        context.write_json(
            self.req / "sds_design.json",
            {"target_table": {"name": "someone_else_t", "layer": "ads"}},
        )
        issues = validators.validate_phase3_data_dev(self.req, state)
        self.assertTrue(any(i.code == "dev.sds_target_not_built" for i in issues), issues)

    # ---- 后半程门：回刷与数据质量（PIPE-004） ----

    def _p5_ready(self, items: list) -> None:
        context.write_text(self.req / "delivery_summary.md", "\n".join(f"- line {i}" for i in range(12)))
        context.write_json(
            self.req / "deploy_plan.json",
            {"ticket_id": "T-0001", "env": "prod", "publish_confirmed": True, "items": items},
        )

    def test_p5_blocks_backfill_without_ledger_entry(self) -> None:
        self._p5_ready(
            [{"action": "backfill", "table": "ads_t", "range_start": "20260901", "range_end": "20260907"}]
        )
        issues = validators.validate_phase5_deploy(self.req, {"mode": "mock"})
        self.assertTrue(any(i.code == "deploy.backfill.no_ledger" for i in issues), issues)
        # 账本存在但范围对不上，也必须拦
        context.write_json(
            context.ledger_dir(self.root) / "backfill_runs.json",
            {"runs": [{"key": "ads_t|20260101|20260107", "status": "SUCCESS", "date": "20260101"}]},
        )
        issues = validators.validate_phase5_deploy(self.req, {"mode": "mock"})
        self.assertTrue(any(i.code == "deploy.backfill.missing" for i in issues), issues)

    def test_p5_passes_backfill_with_matching_ledger_entry(self) -> None:
        self._p5_ready(
            [{"action": "backfill", "table": "ads_t", "range_start": "20260901", "range_end": "20260907"}]
        )
        context.write_json(
            context.ledger_dir(self.root) / "backfill_runs.json",
            {"runs": [{"key": "ads_t|20260901|20260907", "status": "SUCCESS", "date": "20260901"}]},
        )
        issues = validators.validate_phase5_deploy(self.req, {})
        self.assertFalse([i for i in issues if i.code == "deploy.backfill.missing"], issues)

    def test_p5_blocks_dqc_not_deployed(self) -> None:
        self._p5_ready([{"action": "dqc_publish", "table": "ads_t"}])
        issues = validators.validate_phase5_deploy(self.req, {})
        self.assertTrue(any(i.code == "deploy.dqc.not_deployed" for i in issues), issues)
        context.write_json(
            self.req / "dqc_results.json",
            {"results": [{"table": "ads_t", "deployed": True, "rules": ["pk_unique"]}]},
        )
        issues = validators.validate_phase5_deploy(self.req, {})
        self.assertFalse([i for i in issues if i.code == "deploy.dqc.not_deployed"], issues)

    def test_deploy_without_plan_blocks_instead_of_silent_noop(self) -> None:
        """真语料测试教训（2026-10-07，data-warehouse-learning 那轮 live 六阶段）：

        计划文件缺失时，旧代码会"零结果 + ok:true"地空跑，而且读不到 env 连 [已确认发布] 门也一起跳过——
        看起来像发布成功。现在必须显式拦住。
        """
        self.assertFalse((self.req / "deploy_plan.json").exists())
        proc = run_engine(self.root, "deploy", "--ticket", "T-0001", "--comment", "[已确认发布] x")
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("deploy_plan", proc.stdout)

    def test_status_gate_diagnoses_current_phase(self) -> None:
        self.pipeline.state["current_phase"] = 2
        self.pipeline.save()
        proc = run_engine(self.root, "status", "--gate", "--ticket", "T-0001")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("arch.doc.missing", proc.stdout)

    def test_report_governance_summary(self) -> None:
        proc = run_engine(self.root, "report", "--ticket", "T-0001")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('"ticket": "T-0001"', proc.stdout)
        self.assertIn('"evidence"', proc.stdout)
        md = run_engine(self.root, "report", "--ticket", "T-0001", "--write")
        self.assertEqual(md.returncode, 0, md.stdout + md.stderr)
        self.assertTrue((self.req / "governance_report.md").is_file())

    def test_dev_deploy_and_rollback_e2e_command_backend(self) -> None:
        context.set_current_ticket(self.root, "T-0001")
        self.pipeline.set_context("verify_result", "passed")
        self.pipeline.set_context("self_check_result", "passed")
        self.pipeline.set_context("user_confirmed", True)
        self.pipeline.state["current_phase"] = 5
        self.pipeline.save()
        self.pipeline.set_context("verify_result", "passed")
        self.pipeline.set_context("self_check_result", "passed")
        self.pipeline.set_context("user_confirmed", True)
        context.ensure_dir(self.req / "ddl")
        context.ensure_dir(self.req / "etl")
        context.ensure_dir(self.req / "dp_task")
        context.ensure_dir(self.req / "rollback")
        context.write_text(self.req / "ddl" / "ads_t.sql", "CREATE TABLE ads_t (a INT);")
        context.write_text(self.req / "etl" / "ads_t.sql", "SELECT 1;")
        context.write_text(
            self.req / "dp_task" / "ads_t.sql",
            "INSERT INTO TABLE ads_t SELECT ${bizdate};",
        )
        context.write_text(self.req / "rollback" / "ads_t.sql", "DROP TABLE IF EXISTS ads_t;")
        context.write_json(
            self.req / "deploy_plan.json",
            {
                "ticket_id": "T-0001",
                "env": "dev",
                "items": [
                    {
                        "action": "create",
                        "table": "ads_t",
                        "rollback_sql_file": "rollback/ads_t.sql",
                    },
                    {"action": "backfill", "table": "ads_t", "range_start": "20260901", "range_end": "20260902"},
                ],
            },
        )
        self._set_config(
            {
                "mode": "live",
                "platform": {
                    "kind": "command",
                    "base_env": "dev",
                    "command": {
                        "engine": "probe",
                        "run": [sys.executable, "-c", "import sys", "{file}"],
                    },
                },
            }
        )
        deploy = run_engine(self.root, "deploy", "--ticket", "T-0001", "--comment", "")
        self.assertEqual(deploy.returncode, 0, deploy.stdout + deploy.stderr)
        self.assertTrue((self.req / "delivery_summary.md").is_file())
        deploy_log = context.load_json(self.req / "deploy_log.json") or {}
        self.assertEqual(len(deploy_log.get("results") or []), 2)
        self.assertTrue(Pipeline(self.req).context_key("deploy_executed"))
        rollback = run_engine(self.root, "rollback", "--ticket", "T-0001", "--comment", "[已回滚] x", "--env", "dev")
        self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
        self.assertTrue((self.req / "rollback_log.json").is_file())

    def test_plaintext_password_triggers_config_warn(self) -> None:
        self._set_config({"platform": {"kind": "cli", "password": "hunter2"}})
        proc = run_engine(self.root, "status", "--ticket", "T-0001")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("config_warns", proc.stdout)
        self.assertIn("password_env", proc.stdout)


class CodeDiffTriggerTests(unittest.TestCase):
    """code_diff 强校验的触发点：改现有任务 / 抓取线上基准。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        desc = (
            "## 背景\nx\n\n## 目标\nx\n\n## 范围\n目标表: ads_t\n来源: dwd_t\n\n"
            "## 验收口径\n验收: ok\n\n## 风险\nx\n\n负责人: a\n计划日期: 2026-09-30\n"
        )
        self.req = context.ticket_dir(self.root, "T-0001")
        Pipeline.init(self.req, "T-0001", "【数据需求】基准测试", desc, context.sha256_text(desc))
        pipeline = Pipeline(self.req)
        pipeline.state["current_phase"] = 3  # create/update_task 受 phase_entry_guard 约束
        # update-task 受 submit_guard 约束：先补齐真跑证据三键（真实流程里由证据门写入）
        pipeline.set_context("verify_result", "passed")
        pipeline.set_context("self_check_result", "passed")
        pipeline.set_context("user_confirmed", True)
        pipeline.save()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_task(self, body: str) -> Path:
        path = self.req / "dp_task" / "ads_t.sql"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def test_update_task_marks_code_diff_required(self) -> None:
        path = self._write_task("-- table: ads_t\nINSERT INTO ads_t SELECT 1;\n")
        created = run_engine(self.root, "platform", "create-task", "--name", "ads_t", "--code-file", str(path))
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        self.assertFalse(Pipeline(self.req).context_key("code_diff.required"))

        read = run_engine(self.root, "platform", "read-task", "--name", "ads_t")
        self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
        updated = run_engine(self.root, "platform", "update-task", "--name", "ads_t", "--code-file", str(path))
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
        self.assertTrue(Pipeline(self.req).context_key("code_diff.required"))

    def test_baseline_capture_feeds_diff(self) -> None:
        path = self._write_task("-- table: ads_t\nINSERT INTO ads_t SELECT 1;\n")
        run_engine(self.root, "platform", "create-task", "--name", "ads_t", "--code-file", str(path))

        captured = run_engine(self.root, "baseline", "capture", "--ticket", "T-0001")
        self.assertEqual(captured.returncode, 0, captured.stdout + captured.stderr)
        baseline_file = context.ledger_dir(self.root) / "baseline" / "T-0001" / "dp_task" / "ads_t.sql"
        self.assertTrue(baseline_file.is_file(), captured.stdout)
        self.assertTrue(Pipeline(self.req).context_key("code_diff.required"))

        # 改动当前任务代码后 diff 应基于 runtime baseline 生成 code_diff.html
        self._write_task("-- table: ads_t\nINSERT INTO ads_t SELECT 1, 2;\n")
        diff = run_engine(self.root, "diff", "--ticket", "T-0001")
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)
        self.assertTrue((self.req / "code_diff.html").is_file())
        self.assertIn("ads_t.sql", diff.stdout)

    def test_baseline_show_lists_files(self) -> None:
        path = self._write_task("-- table: ads_t\nINSERT INTO ads_t SELECT 1;\n")
        run_engine(self.root, "platform", "create-task", "--name", "ads_t", "--code-file", str(path))
        run_engine(self.root, "baseline", "capture", "--ticket", "T-0001")
        shown = run_engine(self.root, "baseline", "show", "--ticket", "T-0001")
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        self.assertIn("dp_task/ads_t.sql", shown.stdout)


class SdsEntryTests(unittest.TestCase):
    """P1 入口：`sds enter` 是 CLI 暴露的状态机入口（此前缺失，宿主里只能绕）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        desc = (
            "## 背景\nx\n\n## 目标\nx\n\n## 范围\n目标表: ads_t\n来源: dwd_t\n\n"
            "## 验收口径\n验收: ok\n\n## 风险\nx\n\n负责人: a\n计划日期: 2026-09-30\n"
        )
        self.req = context.ticket_dir(self.root, "T-0001")
        Pipeline.init(self.req, "T-0001", "【数据需求】入口测试", desc, context.sha256_text(desc))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sds_enter_moves_to_phase1(self) -> None:
        proc = run_engine(self.root, "sds", "enter", "--mode", "verify", "--ticket", "T-0001")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        pipeline = Pipeline(self.req)
        self.assertEqual(pipeline.state.get("current_phase"), 1)
        self.assertEqual(pipeline.sds.get("mode"), "verify")
        self.assertEqual(pipeline.sds.get("status"), "in_progress")

    def test_sds_enter_rejects_bad_mode(self) -> None:
        proc = run_engine(self.root, "sds", "enter", "--mode", "whatever", "--ticket", "T-0001")
        self.assertNotEqual(proc.returncode, 0)


class SemanticSummaryTests(unittest.TestCase):
    """语义层摘要必须来自本单产物，不得是写死模板（实机里出现过假模型名/假发布声明）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        desc = (
            "## 背景\nx\n\n## 目标\nx\n\n## 范围\n目标表: dwd_order_province_di\n来源: ods_order_src\n\n"
            "## 验收口径\n验收: ok\n\n## 风险\nx\n\n负责人: a\n计划日期: 2026-09-30\n"
        )
        self.req = context.ticket_dir(self.root, "T-0001")
        Pipeline.init(self.req, "T-0001", "【数据需求】语义摘要", desc, context.sha256_text(desc))
        context.write_text(self.req / "_user_confirmation.md", "[已确认] 用户拍板进入语义层\n")
        context.write_json(
            self.req / "sds_design.json",
            {
                "ticket_id": "T-0001",
                "target_table": {"name": "dwd_order_province_di"},
                "field_mappings": [{"target_field": "avg_amount"}, {"target_field": "order_cnt"}],
                "knowledge_source_uri": "viking://resources/domain/trade/readme.md",
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_summary_uses_real_artifacts(self) -> None:
        proc = run_engine(self.root, "semantic", "--ticket", "T-0001", "--scenario", "metric-dev")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = (self.req / "semantic_update_summary.md").read_text(encoding="utf-8")
        self.assertIn("dwd_order_province_di", text)
        self.assertIn("avg_amount", text)
        self.assertIn("metric-dev", text)
        self.assertIn("mock 离线模式未执行", text)
        self.assertNotIn("dp_metric_model_activity", text)
        self.assertNotIn("[已发布] OV 可见", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
