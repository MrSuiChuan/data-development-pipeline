"""类型映射与 DDL 渲染（COMPAT-004）。

守两类事：
1. **类型是真映射**：每个方言的整型/小数/时间都不许"看起来一样"，Doris 的
   DECIMALV3、ClickHouse 的 Int64/Decimal、PostgreSQL 的 NUMERIC 各不相同；
2. **渲染不许悄悄产出明显语法错的模板**：PostgreSQL 的注释必须是独立语句、
   Doris 的键子句必须在 `)` 之外、MySQL 不能出现 PROPERTIES、Trino 不能用 USING。
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

from core import context, ddl  # noqa: E402


def run_engine(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ENGINE / "ddp.py"), "--root", str(root), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


SPEC = {
    "table": "ads_x",
    "columns": [
        {"name": "user_id", "type": "bigint", "comment": "用户"},
        {"name": "amt", "type": "decimal", "precision": 18, "scale": 2},
        {"name": "rate", "type": "double"},
        {"name": "nick", "type": "string"},
        {"name": "flag", "type": "boolean"},
        {"name": "ds", "type": "date"},
        {"name": "created_at", "type": "timestamp"},
    ],
    "partition_by": ["ds"],
    "primary_key": ["user_id"],
    "comment": "示例表",
    "properties": {"retention": "30"},
}


class TypeMappingTests(unittest.TestCase):
    def test_type_map_covers_every_dialect_and_canonical_type(self) -> None:
        dialects = ddl.dialects_with_types()
        self.assertGreaterEqual(len(dialects), 8)
        for dialect in dialects:
            for canonical in ddl.CANONICAL_TYPES:
                physical = ddl.physical_type(dialect, canonical, {"name": "c", "precision": 18, "scale": 2})
                self.assertTrue(physical.strip(), f"{dialect}/{canonical} 映射为空")

    def test_representative_types_differ_between_engines(self) -> None:
        cases = {
            "doris": ("DECIMALV3(18,2)", "DATETIME"),
            "hive": ("DECIMAL(18,2)", "STRING", "TIMESTAMP"),
            "clickhouse": ("Decimal(18, 2)", "Int64", "DateTime64(3)"),
            "postgres": ("NUMERIC(18,2)", "DOUBLE PRECISION", "TEXT"),
            "trino": ("VARCHAR", "TIMESTAMP", "DOUBLE"),
            "odps": ("DATETIME", "STRING"),
            "mysql": ("DECIMAL(18,2)", "DATETIME", "TINYINT(1)"),
        }
        for dialect, expected in cases.items():
            sql = ddl.render_ddl(SPEC, dialect)["sql"]
            for token in expected:
                self.assertIn(token, sql, f"{dialect} 渲染缺 {token}")

    def test_unknown_type_or_dialect_raises(self) -> None:
        with self.assertRaises(ddl.DdlError) as ctx:
            ddl.render_ddl({"table": "t", "columns": [{"name": "a", "type": "varchar"}]}, "hive")
        self.assertIn("未知 canonical 类型", str(ctx.exception))
        with self.assertRaises(ddl.DdlError) as ctx:
            ddl.render_ddl({"table": "t", "columns": [{"name": "a", "type": "bigint"}]}, "postgresql")
        self.assertIn("未知方言", str(ctx.exception))

    def test_decimal_requires_precision_and_scale(self) -> None:
        with self.assertRaises(ddl.DdlError) as ctx:
            ddl.render_ddl({"table": "t", "columns": [{"name": "a", "type": "decimal"}]}, "hive")
        self.assertIn("precision", str(ctx.exception))

    def test_spec_shape_is_validated(self) -> None:
        for bad in ({}, {"table": "t"}, {"table": "t", "columns": []}, {"columns": [{"name": "a", "type": "bigint"}]}):
            with self.assertRaises(ddl.DdlError):
                ddl.render_ddl(bad, "hive")


class RenderTemplateTests(unittest.TestCase):
    """渲染出来的模板不许有明显语法错（这些坑都是本轮实测踩出来的）。"""

    def test_postgres_comments_are_separate_statements(self) -> None:
        sql = ddl.render_ddl(SPEC, "postgres")["sql"]
        self.assertIn("\nCOMMENT ON TABLE ads_x IS '示例表';", sql)
        self.assertIn("COMMENT ON COLUMN ads_x.user_id IS '用户';", sql)
        head = sql.split("COMMENT ON TABLE", 1)[0]
        self.assertIn(";", head, "CREATE 语句必须先收尾，注释不能粘在建表语句里")
        self.assertNotIn("user_id BIGINT COMMENT", sql, "PostgreSQL 不支持列内联 COMMENT")

    def test_doris_starrocks_key_clause_is_outside_column_list(self) -> None:
        for dialect in ("doris", "starrocks"):
            sql = ddl.render_ddl(SPEC, dialect)["sql"]
            body = sql.split("\n)", 1)[0]
            self.assertNotIn("PRIMARY KEY", body, f"{dialect} 的键子句写进了列定义（语法错）")
            self.assertIn("UNIQUE KEY (user_id)", sql)
            self.assertIn("DISTRIBUTED BY HASH(user_id)", sql)

    def test_mysql_has_no_properties_clause(self) -> None:
        parts = ddl.render_ddl(SPEC, "mysql")
        self.assertNotIn("PROPERTIES", parts["sql"])
        self.assertTrue(any("MySQL 没有表属性语法" in w for w in parts["warnings"]))

    def test_trino_uses_with_not_using(self) -> None:
        sql = ddl.render_ddl(SPEC, "trino")["sql"]
        self.assertNotIn("USING ", sql)
        # 真实引擎教训：不要凭空生成 format（memory connector 会报 unknown property）
        self.assertNotIn("format = ", sql)
        self.assertIn("partitioned_by = ARRAY['ds']", sql)

    def test_trino_without_partition_or_properties_has_no_with_clause(self) -> None:
        """memory/JDBC 这类 connector 不认 format/partitioned_by：没有可写属性时就不该出 WITH。"""
        spec = {"table": "t", "columns": [{"name": "id", "type": "bigint"}]}
        sql = ddl.render_ddl(spec, "trino")["sql"]
        self.assertNotIn("WITH (", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS t (", sql)

    def test_trino_partition_warns_about_connector_specific_property(self) -> None:
        result = ddl.render_ddl(SPEC, "trino")
        self.assertTrue(any("partitioned_by" in w and "connector" in w for w in result["warnings"]))

    def test_flink_clause_order(self) -> None:
        sql = ddl.render_ddl(SPEC, "flink")["sql"]
        self.assertLess(sql.index("COMMENT '示例表'"), sql.index("PARTITIONED BY"))
        self.assertLess(sql.index("PARTITIONED BY"), sql.index("WITH ("))

    def test_hive_and_odps_partition_columns_are_string(self) -> None:
        for dialect in ("hive", "odps"):
            sql = ddl.render_ddl(SPEC, dialect)["sql"]
            self.assertIn(f"PARTITIONED BY (ds STRING)", sql)

    def test_hive_partition_column_is_not_duplicated(self) -> None:
        """真实 Hive 会报 Duplicate column name：分区列只能出现在 PARTITIONED BY 里。

        SPEC 的 columns 里带 ds（PG/ClickHouse 需要它是普通列），渲染 hive 时必须把它从
        列定义里拿掉，并说明为什么。
        """
        result = ddl.render_ddl(SPEC, "hive")
        body = result["sql"].split("PARTITIONED BY", 1)[0]
        self.assertNotIn("ds DATE", body)
        self.assertIn("PARTITIONED BY (ds STRING)", result["sql"])
        self.assertTrue(any("分区列" in w and "Duplicate" in w for w in result["warnings"]), result["warnings"])

        # odps 同规则
        odps = ddl.render_ddl(SPEC, "odps")
        self.assertNotIn("ds DATE", odps["sql"].split("PARTITIONED BY", 1)[0])

        # 其它方言保留该列（它们需要分区键是真实列）
        for dialect in ("clickhouse", "postgres", "spark"):
            sql = ddl.render_ddl(SPEC, dialect)["sql"]
            self.assertIn("ds", sql.split(")", 1)[0], dialect)

    def test_timestamp_semantics_warning_is_attached(self) -> None:
        for dialect in ("mysql", "odps", "flink", "clickhouse", "postgres"):
            warnings = ddl.render_ddl(SPEC, dialect)["warnings"]
            self.assertTrue(any("时区" in w or "语义" in w for w in warnings), dialect)

    def test_every_dialect_renders_statement_terminated(self) -> None:
        for dialect in ddl.dialects_with_types():
            sql = ddl.render_ddl(SPEC, dialect)["sql"]
            self.assertTrue(sql.rstrip().endswith(";"), dialect)

    def test_postgres_partition_gets_default_partition(self) -> None:
        """真实联调踩到：只写 PARTITION BY RANGE 不建分区，写入会报 no partition of relation found。"""
        result = ddl.render_ddl(SPEC, "postgres")
        self.assertIn("PARTITION BY RANGE (ds)", result["sql"])
        self.assertIn("CREATE TABLE IF NOT EXISTS ads_x_default PARTITION OF ads_x DEFAULT;", result["sql"])
        self.assertTrue(any("DEFAULT 分区" in w for w in result["warnings"]))


class DdlTypeCheckTests(unittest.TestCase):
    def test_cross_engine_types_are_flagged(self) -> None:
        sql = "CREATE TABLE t (\n  a DECIMALV3(18,2),\n  b BIGINT\n);"
        flagged = ddl.validate_ddl_types(sql, "hive")
        self.assertEqual(flagged, ["a: DECIMALV3"])
        self.assertEqual(ddl.validate_ddl_types(sql, "doris"), [])

    def test_unknown_dialect_in_type_check_raises(self) -> None:
        with self.assertRaises(ddl.DdlError):
            ddl.validate_ddl_types("CREATE TABLE t (a BIGINT);", "sqlserver")


class DdlCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _spec_file(self, payload: dict) -> Path:
        path = self.root / "spec.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_ddl_types_lists_canonical_and_dialects(self) -> None:
        proc = run_engine(self.root, "ddl", "types")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertIn("decimal", payload["canonical"])
        self.assertIn("clickhouse", payload["dialects"])

    def test_render_prints_sql_and_refuses_to_overwrite(self) -> None:
        spec = self._spec_file(SPEC)
        proc = run_engine(self.root, "ddl", "render", "--spec", str(spec), "--dialect", "hive")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertIn("CREATE TABLE IF NOT EXISTS ads_x", payload["sql"])
        self.assertEqual(payload["dialect"], "hive")

        out = self.root / "ddl" / "ads_x.sql"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("-- 已有内容\n", encoding="utf-8")
        blocked = run_engine(self.root, "ddl", "render", "--spec", str(spec), "--dialect", "hive", "--out", str(out))
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("已存在", blocked.stdout)
        self.assertEqual(out.read_text(encoding="utf-8"), "-- 已有内容\n")

        forced = run_engine(
            self.root, "ddl", "render", "--spec", str(spec), "--dialect", "hive", "--out", str(out), "--force"
        )
        self.assertEqual(forced.returncode, 0, forced.stdout)
        self.assertIn("CREATE TABLE IF NOT EXISTS ads_x", out.read_text(encoding="utf-8"))

    def test_bad_spec_is_blocked_with_reason(self) -> None:
        spec = self._spec_file({"table": "ads_x", "columns": [{"name": "a", "type": "varchar"}]})
        proc = run_engine(self.root, "ddl", "render", "--spec", str(spec), "--dialect", "hive")
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["gate"], "ddl_render")
        self.assertIn("未知 canonical 类型", payload["reason"])

    def test_dialect_defaults_to_project_config(self) -> None:
        cfg = context.load_config(self.root)
        cfg["platform"] = {"kind": "clickhouse", "command": {"run": ["clickhouse-client", "{file}"]}}
        context.write_json(context.config_path(self.root), cfg)
        spec = self._spec_file(SPEC)
        proc = run_engine(self.root, "ddl", "render", "--spec", str(spec))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("ENGINE = MergeTree", json.loads(proc.stdout)["sql"])


if __name__ == "__main__":
    unittest.main()
