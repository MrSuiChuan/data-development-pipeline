"""湖表格式维度（COMPAT-011）：格式与执行引擎正交，组合未登记就报错。

守三件事：
1. 注册表与格式表互相对得上（kind.formats ↔ formats.FORMATS[f].dialects）；
2. 渲染按格式出真东西（spark 用 USING iceberg/hudi/delta、flink 补 connector、trino 说明 catalog）；
3. 组合不合法（hive+iceberg、未知格式）直接报错并列出可用项，不是"忽略 format 继续跑"。
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

from core import context, ddl, engines, formats, validators  # noqa: E402


def run_engine(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENGINE / "ddp.py"), "--root", str(root), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


SPEC = {
    "table": "ods_orders",
    "columns": [{"name": "order_id", "type": "bigint"}, {"name": "ds", "type": "date"}],
    "partition_by": ["ds"],
    "primary_key": ["order_id"],
    "properties": {},
}


class FormatRegistryTests(unittest.TestCase):
    def test_format_registry_is_valid(self) -> None:
        self.assertEqual(formats.validate_registry(), [])
        self.assertIn("iceberg", formats.available_formats())
        self.assertIn("none", formats.available_formats())

    def test_engine_and_format_registries_agree(self) -> None:
        for kind, spec in engines.ENGINES.items():
            for fmt in spec.formats:
                if fmt == "none":
                    continue
                self.assertIn(fmt, formats.FORMATS, f"{kind} 声明了未登记的格式 {fmt}")
                self.assertIn(
                    spec.dialect, formats.FORMATS[fmt].dialects,
                    f"{kind}({spec.dialect}) 声明支持 {fmt}，但格式表没把这个方言列入",
                )
        for name, spec in formats.FORMATS.items():
            for dialect in spec.dialects:
                self.assertIn(dialect, {s.dialect for s in engines.ENGINES.values()}, f"{name}: 未知方言 {dialect}")

    def test_validate_format_rejects_unsupported_combination(self) -> None:
        engines.validate_format("spark", "iceberg")  # 合法：不抛
        with self.assertRaises(engines.UnknownEngine) as ctx:
            engines.validate_format("hive", "iceberg")
        self.assertIn("可用", str(ctx.exception))
        with self.assertRaises(engines.UnknownEngine):
            engines.validate_format("spark", "snowflake")


class FormatRenderTests(unittest.TestCase):
    def test_spark_lakehouse_formats_render_using_clause(self) -> None:
        for fmt in ("iceberg", "hudi", "delta"):
            sql = ddl.render_ddl(SPEC, "spark", fmt_override=fmt)["sql"]
            self.assertIn(f"USING {fmt}", sql, fmt)

    def test_flink_format_injects_connector(self) -> None:
        result = ddl.render_ddl(SPEC, "flink", fmt_override="paimon")
        self.assertIn("'connector'='paimon'", result["sql"])
        self.assertEqual(result["format"], "paimon")

    def test_trino_explains_catalog_for_lakehouse(self) -> None:
        result = ddl.render_ddl(SPEC, "trino", fmt_override="iceberg")
        # 真跑教训：不要凭空生成 format（memory connector 报 unknown property）；
        # Trino 的表格式由 catalog 决定，DDL 里只保留显式给的属性。
        self.assertNotIn("format = ", result["sql"])
        self.assertTrue(any("catalog" in w or "connector" in w for w in result["warnings"]))

    def test_render_carries_write_and_ops_guidance(self) -> None:
        for fmt in formats.FORMATS:
            result = ddl.render_ddl(SPEC, formats.FORMATS[fmt].dialects[0], fmt_override=fmt)
            joined = " ".join(result["warnings"])
            self.assertIn("写入", joined, fmt)
            self.assertIn("运维", joined, fmt)

    def test_unsupported_combination_is_blocked(self) -> None:
        for dialect, fmt in (("hive", "iceberg"), ("mysql", "delta"), ("clickhouse", "hudi")):
            with self.assertRaises(ddl.DdlError) as ctx:
                ddl.render_ddl(SPEC, dialect, fmt_override=fmt)
            self.assertIn("未登记表格式", str(ctx.exception), f"{dialect}+{fmt}")
        with self.assertRaises(ddl.DdlError):
            ddl.render_ddl(SPEC, "spark", fmt_override="hudi2")


class FormatCheckTests(unittest.TestCase):
    def test_hudi_without_primary_key_is_flagged(self) -> None:
        hits = formats.format_issues("CREATE TABLE t (id BIGINT) USING hudi TBLPROPERTIES ('type'='MOR');", "hudi")
        self.assertEqual([h["id"] for h in hits], ["format.hudi.missing_primary_key"])
        ok = formats.format_issues(
            "CREATE TABLE t (id BIGINT) USING hudi TBLPROPERTIES ('primaryKey'='id','preCombineField'='ts');", "hudi"
        )
        self.assertEqual([h for h in ok if h["id"] == "format.hudi.missing_primary_key"], [])

    def test_other_format_checks(self) -> None:
        self.assertIn("format.delta.vacuum", [h["id"] for h in formats.format_issues("VACUUM t;", "delta")])
        self.assertIn("format.iceberg.row_delete", [h["id"] for h in formats.format_issues("DELETE FROM t WHERE ds='1';", "iceberg")])
        self.assertIn(
            "format.paimon.missing_bucket",
            [h["id"] for h in formats.format_issues("CREATE TABLE t (a INT) WITH ('connector'='paimon');", "paimon")],
        )
        self.assertEqual(formats.format_issues("VACUUM t;", "none"), [])

    def test_checks_are_warn_only(self) -> None:
        for fmt in formats.FORMATS:
            for check in formats.FORMATS[fmt].checks:
                self.assertEqual(check.severity, "WARN", f"{fmt}/{check.id} 用了 FAIL：格式差异不该阻断流水线")

    def test_scan_sql_tree_picks_format_from_config(self) -> None:
        """P3 出口门要按配置里的 platform.format 检查，而不是只看 sql check。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context.ensure_app_dir(root, mode="mock")
            cfg = context.load_config(root)
            cfg["platform"] = {"kind": "spark", "format": "hudi",
                               "command": {"run": ["spark-sql", "-f", "{file}"]}}
            context.write_json(context.config_path(root), cfg)
            req = root / ".data-dev" / "tickets" / "T-1"
            for sub in ("ddl", "etl", "dp_task"):
                (req / sub).mkdir(parents=True)
            hudi_ddl = "CREATE TABLE IF NOT EXISTS ods_x USING hudi PARTITIONED BY (ds);"
            (req / "ddl" / "ods_x.sql").write_text(hudi_ddl, encoding="utf-8")
            (req / "etl" / "ods_x.sql").write_text(hudi_ddl, encoding="utf-8")
            (req / "dp_task" / "ods_x.sql").write_text(hudi_ddl, encoding="utf-8")
            codes = [i.code for i in validators.scan_sql_tree(req)]
            self.assertIn("format.hudi.missing_primary_key", codes)


class FormatConfigTests(unittest.TestCase):
    def test_doctor_validates_format_combination(self) -> None:
        good = engines.diagnose_platform({"kind": "spark", "format": "iceberg",
                                          "command": {"run": ["spark-sql", "-f", "{file}"]}})
        self.assertTrue(good["ok"], good["errors"])
        self.assertEqual(good["format"], "iceberg")
        bad = engines.diagnose_platform({"kind": "hive", "format": "iceberg",
                                         "command": {"run": ["beeline", "-f", "{file}"]}})
        self.assertFalse(bad["ok"])
        self.assertIn("未登记表格式", bad["errors"][0])
        unknown = engines.diagnose_platform({"kind": "spark", "format": "snowflake",
                                             "command": {"run": ["spark-sql", "-f", "{file}"]}})
        self.assertFalse(unknown["ok"])

    def test_sql_check_cli_uses_format_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context.ensure_app_dir(root, mode="mock")
            # 走 --file（真实路径）：带 -- source: 是基线第 9 项的要求，这里要验的是格式规则
            sql_file = root / "ddl_t.sql"
            sql_file.write_text(
                "-- table: t\n-- source: ods_raw\n"
                "CREATE TABLE IF NOT EXISTS t (id BIGINT) USING hudi PARTITIONED BY (ds);\n",
                encoding="utf-8",
            )
            plain = run_engine(root, "sql", "check", "--file", str(sql_file))
            self.assertEqual(plain.returncode, 0, plain.stdout)
            self.assertNotIn("format.hudi", plain.stdout)

            flagged = run_engine(root, "sql", "check", "--file", str(sql_file), "--format", "hudi")
            self.assertEqual(flagged.returncode, 0, flagged.stdout)  # WARN 不阻断
            payload = json.loads(flagged.stdout)
            self.assertEqual(payload["format"], "hudi")
            self.assertIn("format.hudi.missing_primary_key", [i["code"] for i in payload["issues"]])


if __name__ == "__main__":
    unittest.main()
