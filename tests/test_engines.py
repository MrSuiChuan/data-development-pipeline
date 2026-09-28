"""多引擎兼容：注册表是唯一权威、按方言检查、配置体检（COMPAT-001/002/003/006）。

这些用例守的是三件容易被"顺手改回去"的事：
1. 引擎清单只有一份（engines.py），clients 的两个元组只是它的投影；
2. 未登记 kind 必须硬报错——不许再静默落回 LivePlatformClient；
3. 不传 dialect 时基线 17 项行为与改动前一致（方言规则是"加一档"而不是"改一档"）。
"""

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

from core import clients, context, dialects, engines, sql_platforms, validators  # noqa: E402


def run_engine(root: Path, *argv: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ENGINE / "ddp.py"), "--root", str(root), *argv]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


class RegistryTests(unittest.TestCase):
    """COMPAT-001 / COMPAT-002：注册表结构、投影唯一性、未登记 kind 的硬错误。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="live")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_platform(self, platform: dict) -> None:
        cfg = context.load_config(self.root)
        cfg["mode"] = "live"
        cfg["platform"] = platform
        context.write_json(context.config_path(self.root), cfg)

    def test_registry_is_structurally_valid(self) -> None:
        self.assertEqual(engines.validate_registry(), [])
        self.assertEqual(dialects.validate_rules(), [])

    def test_mainstream_engines_are_registered(self) -> None:
        for kind in ("hive", "spark", "flink", "trino", "presto", "clickhouse",
                     "postgres", "greenplum", "odps", "paimon", "doris", "starrocks",
                     "mysql", "tidb", "oceanbase", "generic-sql", "command", "cli", "mcp"):
            self.assertIn(kind, engines.available_kinds(), kind)
        # 湖表格式与执行引擎正交：同一格式挂在多个方言下
        self.assertIn("iceberg", engines.formats_of("spark"))
        self.assertIn("paimon", engines.formats_of("flink"))
        self.assertEqual(engines.formats_of("hive"), ("none",))

    def test_alias_resolves_without_fuzzy_match(self) -> None:
        self.assertEqual(engines.canonical_kind("maxcompute"), "odps")
        self.assertEqual(engines.resolve("MaxCompute").dialect, "odps")
        with self.assertRaises(engines.UnknownEngine):
            engines.resolve("click-house")  # 近似拼写不放行

    def test_required_keys_are_engine_specific(self) -> None:
        self.assertEqual(engines.resolve("doris").missing_keys({}), ["host"])
        self.assertEqual(engines.resolve("doris").missing_keys({"host": "h"}), [])
        self.assertEqual(engines.resolve("hive").missing_keys({"host": "h"}), ["command.run"])
        self.assertEqual(engines.resolve("hive").missing_keys({"command": {"run": ["beeline"]}}), [])

    def test_command_engine_decides_dialect(self) -> None:
        """kind=command 的真实方言来自 platform.command.engine。

        真实引擎联调发现：只看 kind 会把 MySQL/Hive 的 DDL 与检查全退化成 generic
        （建表丢了 PRIMARY KEY、方言规则一条不生效）。
        """
        config = {"platform": {"kind": "command", "command": {"engine": "mysql", "run": ["mysql"]}}}
        self.assertEqual(engines.dialect_from_config(config), "mysql")
        self.assertEqual(engines.dialect_from_config({"platform": {"kind": "command", "command": {}}}), "generic")
        with self.assertRaises(engines.UnknownEngine):
            engines.dialect_from_config({"platform": {"kind": "command", "command": {"engine": "oracle"}}})
        # doctor 的 dialect 必须与渲染/检查口径一致
        report = engines.diagnose_platform({"kind": "command", "command": {"engine": "mysql", "run": ["mysql"]}})
        self.assertEqual(report["dialect"], "mysql")
        self.assertTrue(report["ok"], report["errors"])

    def test_clients_tuples_are_projections_of_the_registry(self) -> None:
        self.assertEqual(clients.SQL_PLATFORM_KINDS, engines.kinds_for_family(engines.FAMILY_DBAPI))
        self.assertEqual(clients.COMMAND_PLATFORM_KINDS, engines.kinds_for_family(engines.FAMILY_COMMAND))
        for kind in clients.SQL_PLATFORM_KINDS:
            self.assertNotIn(kind, clients.COMMAND_PLATFORM_KINDS)

    def test_unknown_kind_is_a_hard_error_not_a_fallback(self) -> None:
        self._write_platform({"kind": "clickhouse2", "host": "h"})
        with self.assertRaises(clients.ClientError) as ctx:
            clients.make_platform(context.load_config(self.root), self.root)
        message = str(ctx.exception)
        self.assertIn("未登记的 platform.kind", message)
        self.assertIn("clickhouse", message)  # 报错要给出可用项

    def test_every_registered_kind_builds_an_adapter_of_its_family(self) -> None:
        for kind in engines.available_kinds():
            self._write_platform({"kind": kind, "host": "h", "command": {"run": ["probe", "{file}"]}})
            config = context.load_config(self.root)
            spec = engines.resolve(kind)
            if spec.family == engines.FAMILY_MCP:
                with self.assertRaises(clients.ClientError):
                    clients.make_platform(config, self.root)
                continue
            client = clients.make_platform(config, self.root)
            if spec.family == engines.FAMILY_DBAPI:
                self.assertIsInstance(client, sql_platforms.SqlPlatformClient, kind)
            elif spec.family == engines.FAMILY_COMMAND:
                self.assertIsInstance(client, sql_platforms.CommandPlatformClient, kind)
            else:
                self.assertIsInstance(client, clients.LivePlatformClient, kind)

    def test_evidence_level_is_declared_and_stamped(self) -> None:
        self.assertEqual(engines.evidence_of("cli"), engines.EVIDENCE_SERVER)
        for kind in ("doris", "hive", "clickhouse", "spark"):
            self.assertEqual(engines.evidence_of(kind), engines.EVIDENCE_LOCAL, kind)
        self._write_platform({"kind": "doris", "host": "h"})

        class RecordingSql(sql_platforms.SqlPlatformClient):
            def _raw_execute(self, sql, env=None):
                return self._receipt(sql, 0.0, {"affected_rows": 1})

        record = RecordingSql(context.load_config(self.root), self.root).run_sql("SELECT 1", env="dev")
        self.assertEqual(record["evidence_level"], engines.EVIDENCE_LOCAL)
        self.assertTrue(str(record["query_id"]).startswith("run_"))

    def test_evidence_level_surfaces_in_gate_issues(self) -> None:
        """级别不能只写在回执里：出口门必须把它带进结论（否则报告里看不出来）。"""
        cases = (
            ({"status": "SUCCESS", "query_id": "run_x", "evidence_level": engines.EVIDENCE_LOCAL},
             "evidence.level.local_receipt"),
            ({"status": "SUCCESS", "query_id": "q_x", "evidence_level": engines.EVIDENCE_MOCK},
             "evidence.level.mock"),
            ({"status": "SUCCESS", "query_id": "q_x"}, "evidence.level.unlabeled"),
        )
        for receipt, expected in cases:
            codes = [i.code for i in validators.evidence_level_issues(receipt, 3, "etl_dev_exec.json")]
            self.assertIn(expected, codes, receipt)
            self.assertTrue(all(i.severity == "WARN" for i in validators.evidence_level_issues(receipt, 3, "f")))
        server = {"status": "SUCCESS", "query_id": "q_x", "evidence_level": engines.EVIDENCE_SERVER}
        self.assertEqual(validators.evidence_level_issues(server, 3, "f"), [])
        self.assertEqual(validators.evidence_level_issues(None, 3, "f"), [])


class DialectRuleTests(unittest.TestCase):
    """COMPAT-003：方言规则的判定与"不传 dialect 就完全不变"。"""

    def _severities(self, text: str, dialect: str = None) -> dict:
        issues = validators.sql_compliance_check(text, filename="t.sql", dialect=dialect)
        return {i.code: i.severity for i in issues}

    def test_baseline_is_unchanged_without_dialect(self) -> None:
        sql = "DELETE FROM orders WHERE ds='20260901';"
        baseline = self._severities(sql)
        self.assertNotIn("dialect.hive.no_row_write", baseline)
        self.assertNotIn("dialect.doris.row_write_needs_key_model", baseline)
        # 显式 generic 与不传等价
        self.assertEqual(self._severities(sql, "generic"), baseline)

    def test_hive_rejects_row_level_writes(self) -> None:
        hits = self._severities("DELETE FROM orders WHERE ds='20260901';", "hive")
        self.assertEqual(hits["dialect.hive.no_row_write"], "FAIL")
        overwrite = self._severities("INSERT OVERWRITE TABLE orders PARTITION(ds='20260901') SELECT 1;", "hive")
        self.assertNotIn("FAIL", [v for k, v in overwrite.items() if k.startswith("dialect.")])

    def test_trino_and_mysql_reject_insert_overwrite(self) -> None:
        self.assertEqual(self._severities("INSERT OVERWRITE t SELECT 1;", "trino")["dialect.trino.no_insert_overwrite"], "FAIL")
        self.assertEqual(self._severities("INSERT OVERWRITE t SELECT 1;", "mysql")["dialect.mysql.no_insert_overwrite"], "FAIL")
        self.assertNotIn("dialect.mysql.no_insert_overwrite",
                         self._severities("INSERT OVERWRITE t SELECT 1;", "doris"))

    def test_clickhouse_update_from_and_async_mutation(self) -> None:
        sql = "UPDATE t SET a = 1 FROM s WHERE t.id = s.id;"
        self.assertEqual(self._severities(sql, "clickhouse")["dialect.clickhouse.update_from"], "FAIL")
        mutation = "ALTER TABLE t UPDATE a = 1 WHERE ds = '20260901';"
        self.assertEqual(self._severities(mutation, "clickhouse")["dialect.clickhouse.mutation_async"], "WARN")

    def test_odps_and_flink_reject_row_level_writes(self) -> None:
        self.assertEqual(self._severities("DELETE FROM t WHERE ds='20260901';", "odps")["dialect.odps.no_row_write"], "FAIL")
        self.assertEqual(self._severities("DELETE FROM t WHERE ds='20260901';", "flink")["dialect.flink.no_row_write"], "FAIL")

    def test_conditional_writes_are_warn_not_fail(self) -> None:
        for dialect, sql in (("doris", "DELETE FROM t WHERE ds='20260901';"),
                             ("spark", "MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET t.a=s.a;"),
                             ("postgres", "TRUNCATE TABLE t;")):
            hits = self._severities(sql, dialect)
            dialect_hits = {k: v for k, v in hits.items() if k.startswith("dialect.")}
            self.assertTrue(dialect_hits, dialect)
            self.assertTrue(all(v == "WARN" for v in dialect_hits.values()), (dialect, dialect_hits))

    def test_unknown_dialect_has_no_rules(self) -> None:
        self.assertEqual(dialects.rules_for("postgresql-x"), ())
        self.assertEqual(dialects.dialect_issues("DELETE FROM t;", "postgresql-x"), [])

    def test_lineage_rule_targets_data_movement_not_ddl(self) -> None:
        """第 9 条只该管搬数据的语句。

        真实联调发现：旧写法把 CREATE TABLE 也算进去，导致所有新建表 DDL 被判
        「缺 -- source: 上游血缘注解」——建表被挡在门外（见 docs/TEST-REPORT-postgres-live.md）。
        """
        ddl_sql = "CREATE TABLE IF NOT EXISTS t (\n  id BIGINT,\n  ds DATE\n);"
        ddl_codes = [i.code for i in validators.sql_compliance_check(ddl_sql, filename="t.sql")]
        self.assertNotIn("sql.compliance.no_lineage", ddl_codes)

        insert = "-- table: t\nINSERT INTO t (id) VALUES (1);"
        insert_codes = [i.code for i in validators.sql_compliance_check(insert, filename="t.sql")]
        self.assertIn("sql.compliance.no_lineage", insert_codes)

        ctas = "-- table: t\nCREATE TABLE t AS SELECT id FROM src;"
        ctas_codes = [i.code for i in validators.sql_compliance_check(ctas, filename="t.sql")]
        self.assertNotIn("sql.compliance.no_lineage", ctas_codes, "CTAS 有 FROM，不该再要 source 注解")

    def test_target_table_parser_handles_if_not_exists(self) -> None:
        """真实 P3 出口门踩到：`CREATE TABLE IF NOT EXISTS x` 曾把 "IF" 解析成目标表。"""
        for sql, expected in (
            ("CREATE TABLE IF NOT EXISTS ods_x (a BIGINT);", "ods_x"),
            ("CREATE TABLE ods_y (a BIGINT);", "ods_y"),
            ("INSERT OVERWRITE TABLE ads_z SELECT 1;", "ads_z"),
            ("INSERT OVERWRITE ads_w SELECT 1;", "ads_w"),
            ("INSERT INTO TABLE ads_v SELECT 1;", "ads_v"),
        ):
            self.assertEqual(validators._first_target_table(sql), expected, sql)

        # 渲染模板 + 文件名一致时，第 1 项不该再报 name_mismatch
        rendered = "CREATE TABLE IF NOT EXISTS ods_x (\n  a BIGINT\n);"
        codes = [
            i.code
            for i in validators.sql_compliance_check(rendered, filename="ods_x.sql")
        ]
        self.assertNotIn("sql.compliance.name_mismatch", codes)

    def test_sql_check_cli_reports_and_blocks_by_dialect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context.ensure_app_dir(root, mode="mock")
            bad = run_engine(root, "sql", "check", "--text", "DELETE FROM t WHERE ds='20260901';", "--dialect", "hive")
            self.assertEqual(bad.returncode, 1)
            payload = json.loads(bad.stdout)
            self.assertEqual(payload["dialect"], "hive")
            self.assertFalse(payload["ok"])
            codes = [i["code"] for i in payload["issues"]]
            self.assertIn("dialect.hive.no_row_write", codes)

            ok = run_engine(root, "sql", "check", "--text", "SELECT 1;")
            self.assertEqual(ok.returncode, 0, ok.stdout)
            self.assertEqual(json.loads(ok.stdout)["dialect"], "generic")


class PlatformDoctorTests(unittest.TestCase):
    """COMPAT-006：配置体检（缺键/未登记/明文口令/profiles 形状）。"""

    def test_unknown_kind_is_reported_with_available_list(self) -> None:
        report = engines.diagnose_platform({"kind": "flnik"})
        self.assertFalse(report["ok"])
        self.assertIn("未登记的 platform.kind", report["errors"][0])

    def test_missing_required_keys_are_named(self) -> None:
        report = engines.diagnose_platform({"kind": "hive"})
        self.assertFalse(report["ok"])
        self.assertIn("platform.command.run", report["errors"][0])

    def test_plaintext_password_and_local_receipt_are_warnings(self) -> None:
        report = engines.diagnose_platform({"kind": "doris", "host": "h", "password": "secret"})
        self.assertTrue(report["ok"])
        self.assertEqual(report["dialect"], "doris")
        joined = " ".join(report["warnings"])
        self.assertIn("password_env", joined)
        self.assertIn("local_receipt", joined)

    def test_profiles_shape_is_validated(self) -> None:
        report = engines.diagnose_platform({"kind": "doris", "host": "h", "profiles": {"prod": "oops"}})
        self.assertFalse(report["ok"])
        self.assertIn("profiles.prod", report["errors"][0])
        good = engines.diagnose_platform({"kind": "doris", "host": "h", "profiles": {"prod": {"host": "p"}}})
        self.assertTrue(good["ok"])
        self.assertEqual(good["profiles"], ["prod"])

    def test_cli_doctor_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context.ensure_app_dir(root, mode="mock")
            # 默认 kind=cli：本身没有必填键，doctor 不能编造"没配好"，只能如实说方言是 generic
            default = run_engine(root, "platform", "doctor")
            self.assertEqual(default.returncode, 0, default.stdout)
            self.assertEqual(json.loads(default.stdout)["dialect"], "generic")

            cfg = context.load_config(root)
            cfg["platform"] = {"kind": "doris", "host": "127.0.0.1", "database": "ods"}
            context.write_json(context.config_path(root), cfg)
            good = run_engine(root, "platform", "doctor")
            self.assertEqual(good.returncode, 0, good.stdout)
            self.assertEqual(json.loads(good.stdout)["family"], "dbapi")

    def test_shipped_examples_are_valid_and_registered(self) -> None:
        """engine/templates/platform.example.json 的每个示例都必须过 doctor 且 kind 已登记。"""
        path = ROOT_REPO / "engine" / "templates" / "platform.example.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        examples = {k: v for k, v in payload.items() if not k.startswith("_")}
        self.assertGreaterEqual(len(examples), 5)
        for name, example in examples.items():
            report = engines.diagnose_platform(example)
            self.assertTrue(report["ok"], (name, report["errors"]))
            self.assertIn(engines.canonical_kind(str(example["kind"])), engines.available_kinds(), name)

    def test_docs_do_not_carry_a_second_kind_list(self) -> None:
        """文档里不许再抄一份引擎清单——那正是这次要消掉的漂移源头（COMPAT-002）。"""
        stale = (
            '("mysql", "doris", "starrocks", "tidb", "oceanbase", "generic-sql")',
            '("paimon", "flink", "spark", "trino", "presto", "command")',
        )
        docs = [ROOT_REPO / "README.md", ROOT_REPO / "README.en.md"]
        docs += sorted((ROOT_REPO / "docs").glob("*.md"))
        for path in docs:
            text = path.read_text(encoding="utf-8")
            for needle in stale:
                self.assertNotIn(needle, text, f"{path.name} 又抄了一份引擎清单")


class MultiEngineEndToEndTests(unittest.TestCase):
    """COMPAT-008：同一套流程在多个引擎族上跑通，且方言不匹配时真的被拦。

    用 mock 模式的 demo（不碰真集群），它覆盖 P1→P6 全部门控：
    换引擎族不改产物、不改结论 → 门控与引擎解耦；引擎方言与 SQL 不匹配 → P3 出口真拦，
    而不是只在 `sql check` 里拦（"写的时候过了、出口门放行"是最容易骗过自己的组合）。
    """

    WORKING_KINDS = ("cli", "doris", "hive", "spark", "flink", "clickhouse", "odps")

    def _demo_with_kind(self, kind: str):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        # 先建目录再写平台配置：demo 内部会调 setup，而 setup 现在不覆盖已有 config
        context.ensure_app_dir(root, mode="mock")
        cfg = context.load_config(root)
        cfg["platform"] = {"kind": kind, "host": "h", "command": {"run": ["probe", "{file}"]}}
        context.write_json(context.config_path(root), cfg)
        return tmp, root, run_engine(root, "demo")

    def test_demo_outcome_and_artifacts_are_engine_agnostic(self) -> None:
        signatures = {}
        for kind in self.WORKING_KINDS:
            tmp, root, proc = self._demo_with_kind(kind)
            try:
                self.assertEqual(proc.returncode, 0, f"{kind}: {proc.stdout[-600:]}")
                signatures[kind] = sorted(
                    p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                )
            finally:
                tmp.cleanup()
        baseline = signatures[self.WORKING_KINDS[0]]
        self.assertTrue(baseline)
        for kind, files in signatures.items():
            self.assertEqual(files, baseline, f"{kind} 的产物集合与 cli 不一致")

    def test_demo_is_blocked_when_sql_does_not_fit_the_configured_dialect(self) -> None:
        """demo 的 dp_task 是分区覆盖写法：MySQL 协议族没有 INSERT OVERWRITE → P3 出口必须拦。"""
        tmp, _root, proc = self._demo_with_kind("mysql")
        try:
            self.assertNotEqual(proc.returncode, 0, "MySQL 方言下这份 SQL 不该通过")
            self.assertIn("dialect.mysql.no_insert_overwrite", proc.stdout + proc.stderr)
        finally:
            tmp.cleanup()

    def test_setup_does_not_wipe_existing_platform_config(self) -> None:
        """setup 曾经无条件重写 config.json：用户填好的执行器配置会被静默清掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context.ensure_app_dir(root, mode="mock")
            cfg = context.load_config(root)
            cfg["platform"] = {"kind": "doris", "host": "127.0.0.1", "database": "ods"}
            context.write_json(context.config_path(root), cfg)
            proc = run_engine(root, "setup")
            self.assertEqual(proc.returncode, 0, proc.stdout)
            after = context.load_config(root)
            self.assertEqual(after["platform"]["kind"], "doris")
            self.assertEqual(after["platform"]["database"], "ods")

    def test_adapter_capability_decides_p5_dispatch(self) -> None:
        """P5 曾按配置 kind 判"文件型平台"，mock 模式下于是把任务型客户端当文件型用（崩溃）。"""
        self.assertFalse(clients.MockPlatformClient(Path(".")).file_sql)
        self.assertTrue(sql_platforms.SqlPlatformClient.file_sql)
        self.assertTrue(sql_platforms.CommandPlatformClient.file_sql)
        self.assertFalse(clients.LivePlatformClient.file_sql)

    def test_gate_layer_does_not_branch_on_engine_family(self) -> None:
        """门的判定不该按引擎族分叉：分派只允许发生在 clients/sql_platforms/engines。"""
        for name in ("gates.py", "validators.py", "state.py"):
            text = (ENGINE / "core" / name).read_text(encoding="utf-8")
            self.assertNotIn("engines.FAMILY_", text, f"{name} 按引擎族特判")
            self.assertNotIn("SQL_PLATFORM_KINDS", text, f"{name} 按 kind 清单特判")
            self.assertNotIn("COMMAND_PLATFORM_KINDS", text, f"{name} 按 kind 清单特判")

    def test_matrix_covers_every_registered_kind(self) -> None:
        """文档-注册表一致性（COMPAT-007）：矩阵必须覆盖每个已登记 kind，且不写未登记的。"""
        matrix = (ROOT_REPO / "docs" / "engines" / "README.md").read_text(encoding="utf-8")
        section = matrix.split("## 矩阵", 1)[1].split("## 逐引擎页", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("| ") and line.count("|") >= 6]
        cells = {line.split("|")[1].strip() for line in rows}
        for kind in engines.available_kinds():
            self.assertIn(kind, cells, f"docs/engines/README.md 缺 kind 行: {kind}")
        known = set(engines.available_kinds())
        for cell in cells:
            first = cell.split()[0] if cell.split() else ""
            if first in ("kind", "family", "dialect") or first.startswith("---"):
                continue
            self.assertIn(first, known, f"矩阵里写了未登记的 kind: {cell}")


if __name__ == "__main__":
    unittest.main()
