"""调度注解按目标系统校验（COMPAT-010）。

默认目标必须与改动前**逐字一致**（有非空 `-- schedule:` 就通过）——
这条是回归红线：多目标能力不能顺手把现有项目的 dp_task 判红。
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

from core import context, schedulers, validators  # noqa: E402


def run_engine(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENGINE / "ddp.py"), "--root", str(root), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def codes(sql: str, scheduler: str | None = None) -> list:
    issues = validators.sql_compliance_check(
        sql, filename="ads_x.sql", is_dp_task=True, dialect="generic", scheduler=scheduler
    )
    return [i.code for i in issues]


BASE = "-- table: ads_x\n-- source: dwd_x\n"
TASK = "INSERT OVERWRITE TABLE ads_x PARTITION (ds='${bizdate}')\nSELECT id FROM dwd_x WHERE ds='${bizdate}';\n"


class SchedulerRegistryTests(unittest.TestCase):
    def test_registry_is_valid_and_covers_targets(self) -> None:
        self.assertEqual(schedulers.validate_registry(), [])
        for kind in ("cron", "airflow", "dolphinscheduler", "dataworks", "none"):
            self.assertIn(kind, schedulers.available())

    def test_unknown_kind_lists_available(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            schedulers.resolve("azkaban")
        self.assertIn("可用", str(ctx.exception))


class SchedulerRuleTests(unittest.TestCase):
    def test_default_cron_behaviour_is_unchanged(self) -> None:
        # 没给 scheduler 参数 = 老行为；显式 cron 也必须一样
        for scheduler in (None, "cron"):
            self.assertIn("sql.compliance.no_schedule", codes(BASE + TASK, scheduler))
            with_schedule = codes(BASE + "-- schedule: 0 2 * * *\n" + TASK, scheduler)
            self.assertNotIn("sql.compliance.no_schedule", with_schedule)

    def test_airflow_requires_task_id_and_recommends_timezone(self) -> None:
        missing_id = codes(BASE + "-- schedule: 0 2 * * *\n" + TASK, "airflow")
        self.assertIn("sql.compliance.schedule_required.schedule-id", missing_id)
        full = codes(
            BASE + "-- schedule: 0 2 * * *\n-- schedule-id: ads_x_daily\n-- schedule-timezone: Asia/Shanghai\n" + TASK,
            "airflow",
        )
        self.assertNotIn("sql.compliance.schedule_required.schedule-id", full)
        self.assertNotIn("sql.compliance.schedule_recommended.schedule-timezone", full)
        no_tz = codes(BASE + "-- schedule: 0 2 * * *\n-- schedule-id: ads_x_daily\n" + TASK, "airflow")
        self.assertIn("sql.compliance.schedule_recommended.schedule-timezone", no_tz)

    def test_dolphinscheduler_requires_workflow_id(self) -> None:
        self.assertIn(
            "sql.compliance.schedule_required.schedule-id",
            codes(BASE + "-- schedule: 0 2 * * *\n" + TASK, "dolphinscheduler"),
        )
        self.assertNotIn(
            "sql.compliance.schedule_required.schedule-id",
            codes(BASE + "-- schedule: 0 2 * * *\n-- schedule-id: wf_ads_daily\n" + TASK, "dolphinscheduler"),
        )

    def test_dataworks_recommends_dependencies(self) -> None:
        warn = codes(BASE + "-- schedule: 0 2 * * *\n" + TASK, "dataworks")
        self.assertIn("sql.compliance.schedule_recommended.schedule-depends", warn)
        ok = codes(BASE + "-- schedule: 0 2 * * *\n-- schedule-depends: dwd_x\n" + TASK, "dataworks")
        self.assertNotIn("sql.compliance.schedule_recommended.schedule-depends", ok)

    def test_none_skips_requirement_but_reports_unused_annotation(self) -> None:
        self.assertNotIn("sql.compliance.no_schedule", codes(BASE + TASK, "none"))
        self.assertIn("sql.compliance.schedule_unused", codes(BASE + "-- schedule: 0 2 * * *\n" + TASK, "none"))

    def test_unknown_scheduler_kind_fails_with_available_list(self) -> None:
        issues = validators.sql_compliance_check(
            BASE + TASK, filename="ads_x.sql", is_dp_task=True, scheduler="azkaban"
        )
        kinds = [i.code for i in issues]
        self.assertIn("sql.compliance.scheduler_unknown", kinds)
        message = " ".join(i.message for i in issues if i.code == "sql.compliance.scheduler_unknown")
        self.assertIn("airflow", message)

    def test_annotations_keep_full_value(self) -> None:
        """cron 表达式是多段的：注解解析不能只取第一个 token。"""
        parsed = validators._extract_annotations("-- schedule: 0 2 * * *\n-- schedule-timezone: Asia/Shanghai\n")
        self.assertEqual(parsed["schedule"], "0 2 * * *")
        self.assertEqual(parsed["schedule-timezone"], "Asia/Shanghai")


class SchedulerConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_config_scheduler(self, kind: str) -> None:
        cfg = context.load_config(self.root)
        cfg["scheduler"] = {"kind": kind}
        context.write_json(context.config_path(self.root), cfg)

    def test_default_config_scheduler_is_cron(self) -> None:
        self.assertEqual(context.load_config(self.root)["scheduler"]["kind"], "cron")

    def test_scan_sql_tree_uses_configured_scheduler(self) -> None:
        self._write_config_scheduler("airflow")
        req = self.root / ".data-dev" / "tickets" / "T-1"
        for sub in ("ddl", "etl", "dp_task"):
            (req / sub).mkdir(parents=True)
        body = BASE + "-- schedule: 0 2 * * *\n" + TASK
        for sub in ("ddl", "etl", "dp_task"):
            (req / sub / "ads_x.sql").write_text(body, encoding="utf-8")
        kinds = [i.code for i in validators.scan_sql_tree(req)]
        self.assertIn("sql.compliance.schedule_required.schedule-id", kinds)

    def test_sql_check_cli_reports_scheduler(self) -> None:
        self._write_config_scheduler("none")
        sql_file = self.root / "ads_x.sql"  # 文件名与目标表一致：基线第 1 项要求
        sql_file.write_text(BASE + "-- schedule: 0 2 * * *\n" + TASK, encoding="utf-8")
        proc = run_engine(self.root, "sql", "check", "--file", str(sql_file), "--task")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["scheduler"], "none")
        self.assertIn("sql.compliance.schedule_unused", [i["code"] for i in payload["issues"]])

        explicit = run_engine(self.root, "sql", "check", "--file", str(sql_file), "--task", "--scheduler", "airflow")
        self.assertEqual(explicit.returncode, 1, explicit.stdout)  # airflow 缺 schedule-id 是 FAIL


if __name__ == "__main__":
    unittest.main()
