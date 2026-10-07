"""Tests for pluggable SQL data-source adapters (MySQL family + command engines)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_REPO / "engine"))

from core import clients, context  # noqa: E402
from core.sql_platforms import (  # noqa: E402
    CommandPlatformClient,
    SqlPlatformClient,
    expand_bizdate,
)


class RecordingSqlClient(SqlPlatformClient):
    def __init__(self, config: dict, root: Path):
        super().__init__(config, root)
        self.executed: list[str] = []

    def _raw_execute(self, sql: str, env: str | None = None) -> dict:
        self.executed.append(sql)
        return self._receipt(sql, 0.0, {"affected_rows": 1})


class RecordingCommandClient(CommandPlatformClient):
    def __init__(self, config: dict, root: Path):
        super().__init__(config, root)
        self.executed: list[str] = []

    def _run_command(self, argv: list, sql: str, started: float, label: str, sql_file: Path) -> dict:
        self.executed.append(f"{argv[0]}:{sql[:40]}::{'|'.join(argv)}")
        return self._receipt(sql, 0.0, {"rc": 0})


class SqlAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="live")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_config(self, platform: dict) -> None:
        cfg = context.load_config(self.root)
        cfg["platform"] = platform
        context.write_json(context.config_path(self.root), cfg)

    def test_profile_command_override_is_used_per_env(self) -> None:
        """真机实测（2026-10-07）：argv 只在 __init__ 读一次 → P5 声称部署 prod 实际跑 dev。

        `profiles.prod.command.run` 必须在该环境执行时生效；不写 command 的 profile 要回落到顶层 command。
        """
        self._write_config({
            "kind": "flink", "base_env": "dev", "database": "ddp_dev",
            "command": {"engine": "flink", "run": ["bash", "-lc", "dev-runner {file}"]},
            "profiles": {
                "prod": {"database": "ddp_prod",
                         "command": {"engine": "flink", "run": ["bash", "-lc", "prod-runner {file}"]}},
                "staging": {"database": "ddp_stg"},
            },
        })
        client = RecordingCommandClient(context.load_config(self.root), self.root)
        self.assertEqual(client._argv_for("dev"), ["bash", "-lc", "dev-runner {file}"])
        self.assertIn("prod-runner", " ".join(client._argv_for("prod")))
        self.assertEqual(client._argv_for("staging"), ["bash", "-lc", "dev-runner {file}"],
                         "profile 没写 command 时应回落到顶层 command")

    def test_make_platform_dispatch_mysql_family(self) -> None:
        for kind in ("mysql", "doris", "starrocks", "tidb", "oceanbase", "generic-sql"):
            self._write_config({"kind": kind, "host": "h", "database": "d"})
            self.assertIsInstance(
                clients.make_platform(context.load_config(self.root), self.root),
                SqlPlatformClient,
            )

    def test_make_platform_dispatch_command_family(self) -> None:
        for kind in ("paimon", "flink", "spark", "trino", "presto", "command"):
            self._write_config({"kind": kind, "command": {"engine": kind, "run": ["probe", "{file}"]}})
            self.assertIsInstance(
                clients.make_platform(context.load_config(self.root), self.root),
                CommandPlatformClient,
            )

    def test_mcp_kind_is_reserved_for_session_channel(self) -> None:
        self._write_config({"kind": "mcp", "mcp": {"server": "demo", "tools": {"run_sql": "run_sql"}}})
        with self.assertRaises(clients.ClientError) as ctx:
            clients.make_platform(context.load_config(self.root), self.root)
        self.assertIn("mcp", str(ctx.exception))
        cfg = context.load_config(self.root)
        self.assertEqual((cfg.get("platform") or {}).get("mcp", {}).get("server"), "demo")
        self.assertTrue((cfg.get("platform") or {}).get("mcp", {}).get("evidence_required"))

    def test_placeholder_expansion(self) -> None:
        sql = "INSERT INTO t PARTITION(ds='${bizdate}') SELECT * FROM s WHERE ds='{bizdate}' AND x=$bizdate;"
        out = expand_bizdate(sql, "20260901")
        self.assertNotIn("bizdate", out)
        self.assertEqual(out.count("20260901"), 3)

    def test_sql_run_records_evidence(self) -> None:
        self._write_config({"kind": "doris", "host": "127.0.0.1", "database": "demo"})
        client = RecordingSqlClient(context.load_config(self.root), self.root)
        result = client.run_sql("SELECT 1", env="dev")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(str(result["query_id"]).startswith("run_"))
        self.assertEqual(client.executed, ["SELECT 1"])

    def test_env_without_profile_is_rejected(self) -> None:
        self._write_config({"kind": "doris", "host": "h", "base_env": "dev", "database": "d"})
        client = RecordingSqlClient(context.load_config(self.root), self.root)
        with self.assertRaises(clients.ClientError):
            client._effective_config("prod")

    def test_env_profile_overrides_connection(self) -> None:
        self._write_config(
            {
                "kind": "doris",
                "host": "dev-host",
                "base_env": "dev",
                "database": "dev_db",
                "profiles": {"prod": {"host": "prod-host", "database": "prod_db"}},
            }
        )
        client = RecordingSqlClient(context.load_config(self.root), self.root)
        prod_cfg = client._effective_config("prod")
        self.assertEqual(prod_cfg["host"], "prod-host")
        self.assertEqual(prod_cfg["database"], "prod_db")
        dev_cfg = client._effective_config("dev")
        self.assertEqual(dev_cfg["database"], "dev_db")

    def test_backfill_runs_per_day(self) -> None:
        self._write_config({"kind": "doris", "host": "127.0.0.1", "database": "demo"})
        client = RecordingSqlClient(context.load_config(self.root), self.root)
        code = "INSERT INTO t PARTITION(ds='${bizdate}') SELECT * FROM s WHERE ds='${bizdate}';"
        result = client.backfill_code(code, "20260901", "20260902", task_name="ads_t")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(client.executed), 2)
        self.assertIn("20260901", client.executed[0])
        self.assertIn("20260902", client.executed[1])

    def test_backfill_rejects_bad_range(self) -> None:
        self._write_config({"kind": "mysql", "host": "h"})
        client = RecordingSqlClient(context.load_config(self.root), self.root)
        with self.assertRaises(clients.ClientError):
            client.backfill_code("SELECT ${bizdate};", "20260930", "20260901", task_name="t")

    def test_command_client_runs_argv_with_file(self) -> None:
        self._write_config(
            {"kind": "paimon", "command": {"engine": "paimon", "run": ["flink", "sql", "-f", "{file}"]}}
        )
        client = RecordingCommandClient(context.load_config(self.root), self.root)
        result = client.run_sql("CREATE TABLE t (a INT);", env="dev")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(client.executed), 1)
        self.assertIn("flink", client.executed[0])

    def test_command_client_inline_sql_and_placeholders(self) -> None:
        self._write_config(
            {
                "kind": "command",
                "host": "db1",
                "port": 9030,
                "user": "root",
                "database": "ods",
                "command": {
                    "engine": "mysql",
                    "run": ["mysql", "-h", "{host}", "-P", "{port}", "-u", "{user}",
                            "-D", "{database}", "-e", "{sql}"],
                },
            }
        )
        client = RecordingCommandClient(context.load_config(self.root), self.root)
        client.run_sql("SELECT 1", env="dev")
        call = client.executed[0]
        self.assertIn("mysql", call)
        self.assertIn("db1", call)
        self.assertIn("ods", call)
        self.assertIn("SELECT 1", call)

    def test_task_snapshot_lifecycle(self) -> None:
        self._write_config({"kind": "doris", "host": "h"})
        client = RecordingSqlClient(context.load_config(self.root), self.root)
        client.create_task("ads_t", "SELECT 1", schedule="0 2 * * *")
        client.update_task("ads_t", "SELECT 2")
        self.assertEqual(client.read_task("ads_t")["code"], "SELECT 2")
        client.submit_task("ads_t")
        client.publish_task("ads_t", "ok")
        self.assertEqual(client.read_task("ads_t")["status"], "PUBLISHED")

    def test_live_cli_strict_json_by_default(self) -> None:
        cfg = context.load_config(self.root)
        cfg["cli"] = dict(cfg.get("cli") or {})
        cfg["cli"]["probe"] = sys.executable
        cfg["live"] = {"allow_text": False}
        with self.assertRaises(clients.ClientError):
            clients._run_cli(cfg, self.root, "probe", "-c", "print('not-json')")

    def test_live_cli_allow_text_returns_wrapper(self) -> None:
        cfg = context.load_config(self.root)
        cfg["cli"] = dict(cfg.get("cli") or {})
        cfg["cli"]["probe"] = sys.executable
        cfg["live"] = {"allow_text": True}
        result = clients._run_cli(cfg, self.root, "probe", "-c", "print('not-json')")
        self.assertIn("stdout", result)
        self.assertIn("output_file", result)

    def test_backfill_ledger_keeps_recent_ranges_only(self) -> None:
        self._write_config({"kind": "doris", "host": "h", "base_env": "dev"})
        cfg = context.load_config(self.root)
        cfg["limits"] = dict(cfg.get("limits") or {})
        cfg["limits"]["backfill_ledger_max_keys"] = 1
        context.write_json(context.config_path(self.root), cfg)
        client = RecordingSqlClient(context.load_config(self.root), self.root)
        code = "INSERT INTO TABLE t PARTITION(ds='${bizdate}') SELECT 1;"
        for day in ("20260901", "20260902", "20260903"):
            client.backfill_code(code, day, day, task_name="t")
        ledger = context.load_json(
            context.ledger_dir(self.root) / "backfill_runs.json", {"runs": []}
        ) or {"runs": []}
        keys = {str(r.get("key")) for r in ledger.get("runs", [])}
        self.assertEqual(len(keys), 1)
        self.assertIn("t|20260903|20260903", keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
