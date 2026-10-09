"""T-0002 全流程实跑暴露的插件缺陷 —— 回归用例。

每一条都对应一次真实踩坑（见 .awr/intake/work-ledger.yaml 的 PF-001…PF-006），
断言的是"改用例在旧代码下必须失败"的那个点。
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

from core import spec, validators  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sds(**overrides) -> dict:
    sds = {
        "mode": "verify",
        "declared_source": "dwd.dwd_trade_order_detail_inc",
        "target_table": {"name": "t_x", "layer": "ads", "domain": "commerce", "comment": "c"},
        "needs_intermediate_table": False,
        "field_mappings": [
            {
                "target_field": "k1",
                "source_table": "dwd.d",
                "source_field": "k1",
                "source_layer": "dwd",
                "conclusion": "accurate",
                "sql_evidence": {"join_match_rate": 1.0, "null_rate": 0.0, "key_kind": "dim"},
            }
        ],
        "knowledge_source_uri": "viking://resources/domains/demo/tables/d.md",
        "sql_validation": {"sql_count": 2, "join_match_rate": 1.0, "null_rate": 0.0, "row_count": 3},
        "knowledge_gaps": [],
        "level_suggestion": "L2",
    }
    sds.update(overrides)
    return sds


class BareTableNameTests(unittest.TestCase):
    """PF-001：表名归一，带库名与不带库名是同一张表。"""

    def test_strips_database_prefix_and_noise(self) -> None:
        self.assertEqual(validators.bare_table_name("ads.ads_sku_order_num_top3_d"), "ads_sku_order_num_top3_d")
        self.assertEqual(validators.bare_table_name("  `ADS`.`T_X`  "), "t_x")
        self.assertEqual(validators.bare_table_name("t_x"), "t_x")
        self.assertEqual(validators.bare_table_name(None), "")

    def test_phase3_gate_accepts_qualified_sds_target(self) -> None:
        """SDS 写带库名的目标表、交付表是裸名 —— 旧代码在这里误拦。"""
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "T-9001"
            for sub in ("ddl", "etl", "dp_task"):
                _write(req / sub / "t_x.sql", "-- table: t_x\nSELECT 1;\n")
            _write(req / "sds_design.json", json.dumps(_sds(target_table={
                "name": "ads.t_x", "layer": "ads", "domain": "commerce", "comment": "c"})))
            _write(req / "architecture_doc.md",
                   "L2\n```json\n{\"tables\":[{\"name\":\"t_x\"}]}\n```\n")
            issues = validators.validate_phase3_data_dev(req, {"level": "L2", "limits": {}, "context": {}})
            codes = [i.code for i in issues]
            self.assertNotIn("dev.sds_target_not_built", codes, f"带库名不该被误拦: {codes}")
            self.assertNotIn("dev.arch.table_missing", codes)
            self.assertNotIn("dev.arch.table_extra", codes)

    def test_phase3_gate_still_blocks_a_truly_missing_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "T-9002"
            for sub in ("ddl", "etl", "dp_task"):
                _write(req / sub / "t_x.sql", "-- table: t_x\nSELECT 1;\n")
            _write(req / "sds_design.json", json.dumps(_sds(target_table={
                "name": "ads.t_other", "layer": "ads", "domain": "commerce", "comment": "c"})))
            _write(req / "architecture_doc.md",
                   "L2\n```json\n{\"tables\":[{\"name\":\"t_x\"}]}\n```\n")
            issues = validators.validate_phase3_data_dev(req, {"level": "L2", "limits": {}, "context": {}})
            self.assertIn("dev.sds_target_not_built", [i.code for i in issues])


class BridgeTests(unittest.TestCase):
    """PF-003 / PF-005 / PF-006：口径指纹、level 告警、工单身份兜底。"""

    def _bridge(self, tmp: str, **overrides) -> dict:
        req = Path(tmp) / "T-9003"
        _write(req / "sds_design.json", json.dumps(_sds(**overrides)))
        return spec.bridge_to_spec(req, {"id": "T-9003", "title": "演示", "description": "工单正文"})

    def test_hash_covers_field_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = self._bridge(tmp)["ticket_hash"]
        with tempfile.TemporaryDirectory() as tmp:
            changed = _sds()
            changed["field_mappings"][0]["source_field"] = "另一列"
            out = self._bridge(tmp, field_mappings=changed["field_mappings"])["ticket_hash"]
        self.assertNotEqual(base, out, "改口径必须改 hash，否则准入门的口径校验没有把关能力")

    def test_hash_ignores_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = self._bridge(tmp)["ticket_hash"]
        with tempfile.TemporaryDirectory() as tmp:
            out = self._bridge(tmp, risk_notes=["只是备注"])["ticket_hash"]
        self.assertEqual(base, out, "备注不属于口径，不该改 hash")

    def test_hash_is_not_the_empty_string_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            got = self._bridge(tmp)["ticket_hash"]
        # e3b0c442… 是空串的 sha256，正是这次踩到的假指纹
        self.assertNotEqual(
            got, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_missing_level_returns_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = self._bridge(tmp, level_suggestion=None)
        self.assertIsNone(out["level"])
        self.assertTrue(out.get("warnings"), "缺 level_suggestion 必须给告警，不能静默返回 null")
        self.assertIn("level_suggestion", " ".join(out["warnings"]))

    def test_render_uses_ticket_id_from_directory(self) -> None:
        """SDS 里没有 ticket_id/title 时，抬头不能是「（?）」，正文不能是「工单: None」。"""
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "T-9003"
            _write(req / "sds_design.json", json.dumps(_sds()))
            text = spec.render_sds_spec(req)
        self.assertIn("T-9003", text)
        self.assertNotIn("（?）", text)
        self.assertNotIn("工单: None", text)

    def test_requirement_spec_uses_description_key(self) -> None:
        """tracker 存的是 description；只认 description_md 会让正文与 hash 一起空掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "T-9003"
            _write(req / "sds_design.json", json.dumps(_sds()))
            spec.bridge_to_spec(req, {"id": "T-9003", "title": "演示", "description": "工单正文"})
            body = (req / "requirement_spec.md").read_text(encoding="utf-8")
        self.assertIn("工单正文", body)


class EvidenceSlotTests(unittest.TestCase):
    """PF-002：只读查询的 save-evidence 不能占用 etl 证据槽。"""

    def test_is_write_sql_classification(self) -> None:
        cases = {
            "SELECT 1": False,
            "  with a as (select 1) select * from a": False,
            "SHOW TABLES": False,
            "INSERT INTO t SELECT 1": True,
            "CREATE TABLE t (a int)": True,
            "TRUNCATE TABLE t": True,
            "ALTER TABLE t ADD COLUMN b int": True,
        }
        for sql, expected in cases.items():
            got = self._is_write_sql_via_engine(sql)
            self.assertEqual(got, expected, f"{sql!r} 判定错误")

    @staticmethod
    def _is_write_sql_via_engine(sql: str) -> bool:
        import importlib.util

        spec_ = importlib.util.spec_from_file_location("ddp_engine_under_test", ENGINE / "ddp.py")
        module = importlib.util.module_from_spec(spec_)
        # ddp.py 顶层只做路径与常量初始化，导入即可拿到纯函数
        spec_.loader.exec_module(module)  # type: ignore[union-attr]
        return bool(module._is_write_sql(sql))


if __name__ == "__main__":
    unittest.main()
