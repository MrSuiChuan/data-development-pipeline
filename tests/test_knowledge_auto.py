"""离线任务 finish 后的自动知识回写。"""

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

from core import context, knowledge_ingest  # noqa: E402
from core.state import Pipeline  # noqa: E402

KB_ENGINE = ROOT_REPO.parent / "knowledge-base-plugin" / "engine" / "kbp.py"

DESCRIPTION = """## 背景
用户活跃口径需要统一，自动回写测试。

## 目标
产出一张用户活跃日快照表。

## 范围
目标表: ads_auto_test_di（ads/user）
来源: dwd_user_di

## 验收口径
主键唯一，空值率为 0。

## 风险
下游报表需要回归。

负责人: qa
计划日期: 2026-10-19
"""


class KnowledgeAutoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        context.ensure_app_dir(self.root, mode="mock")
        self.req = context.ticket_dir(self.root, "T-0001")
        pipeline = Pipeline.init(
            self.req,
            "T-0001",
            "【数据需求】自动回写测试",
            DESCRIPTION,
            context.sha256_text(DESCRIPTION),
        )
        context.write_json(
            self.req / "sds_design.json",
            {
                "target_table": {
                    "name": "ads_auto_test_di",
                    "layer": "ads",
                    "domain": "user",
                    "comment": "自动回写测试表",
                },
                "field_mappings": [
                    {
                        "target_field": "user_id",
                        "source_table": "dwd_user_di",
                        "source_field": "user_id",
                        "source_layer": "dwd",
                        "conclusion": "accurate",
                        "sql_evidence": {"join_match_rate": 0.999, "null_rate": 0.0},
                    }
                ],
                "knowledge_source_uri": "viking://resources/domain/user/readme.md",
            },
        )
        context.write_text(
            self.req / "ddl" / "ads_auto_test_di.sql",
            "ALTER TABLE ads_auto_test_di ADD COLUMN user_id STRING;",
        )
        context.write_text(
            self.req / "dp_task" / "ads_auto_test_di.sql",
            "-- table: ads_auto_test_di\n-- schedule: 0 2 * * *\nSELECT 1;\n",
        )
        context.write_text(self.req / "delivery_summary.md", "自动回写测试交付摘要")
        context.write_json(self.req / "test_runs.json", {"runs": []})
        pipeline.state["finished"] = True
        pipeline.state["current_phase"] = 7
        pipeline.save()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_request_from_ticket_artifacts(self) -> None:
        built = knowledge_ingest.build_request(self.root, "T-0001")
        self.assertTrue(built["ok"], built)
        request = built["request"]
        self.assertEqual(request["table_id"], "ads_auto_test_di")
        self.assertEqual(request["domain"], "user")
        self.assertEqual(request["task_status"], "finished")
        self.assertTrue(request["columns"])
        self.assertTrue(request["calibers"])
        self.assertIn("sds_design.json", request["source_artifacts"])

    def test_submit_without_kb_records_non_blocking_failure(self) -> None:
        result = knowledge_ingest.submit(self.root, "T-0001")
        self.assertFalse(result["ok"])
        self.assertEqual(result["gate"], "knowledge_kb_missing")
        self.assertTrue((self.req / "knowledge_update_result.json").is_file())

    @unittest.skipUnless(KB_ENGINE.is_file(), "sibling knowledge-base-plugin not available")
    def test_submit_end_to_end_with_kb(self) -> None:
        kb = self.root / "kb"
        proc = subprocess.run(
            [sys.executable, str(KB_ENGINE), "setup", "--mode", "mock", "--domain", "user", "--root", str(kb)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        (self.root / ".data-dev" / "mock" / "kb").mkdir(parents=True)
        (self.root / ".data-dev" / "mock" / "kb" / "index.json").write_text(
            json.dumps({"documents": []}), encoding="utf-8"
        )
        cfg = context.load_config(self.root)
        cfg["knowledge"]["auto_update"] = {
            "enabled": True,
            "kbp_root": str(kb),
            "kbp_engine": str(KB_ENGINE),
            "timeout_s": 120,
        }
        context.write_json(context.config_path(self.root), cfg)
        result = knowledge_ingest.submit(self.root, "T-0001")
        self.assertTrue(result["ok"], result)
        self.assertIn("ads_auto_test_di", result["uri"])
        index = json.loads(
            (self.root / ".data-dev" / "mock" / "kb" / "index.json").read_text(encoding="utf-8")
        )
        self.assertTrue(any("ads_auto_test_di" in item["uri"] for item in index["documents"]))


class KnowledgeConfigTests(KnowledgeAutoTests):
    """配置与脏输入：这些曾经都会把 finish 拖成 traceback。"""

    def _set_auto_update(self, value: object) -> None:
        cfg = context.load_config(self.root)
        cfg.setdefault("knowledge", {})["auto_update"] = value
        context.write_json(context.config_path(self.root), cfg)

    def test_disabled_auto_update_skips_without_touching_kb(self) -> None:
        self._set_auto_update({"enabled": False})
        result = knowledge_ingest.submit(self.root, "T-0001")
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["gate"], "knowledge_auto_update_disabled")

    def test_zero_timeout_is_reported_not_raised(self) -> None:
        self._set_auto_update({"enabled": True, "timeout_s": 0})
        result = knowledge_ingest.submit(self.root, "T-0001")
        self.assertEqual(result["gate"], "knowledge_config_invalid")
        self.assertTrue(any("timeout_s" in item for item in result["errors"]))

    def test_timeout_type_error_is_reported(self) -> None:
        self._set_auto_update({"enabled": True, "timeout_s": {"bad": 1}})
        result = knowledge_ingest.submit(self.root, "T-0001")
        self.assertEqual(result["gate"], "knowledge_config_invalid")

    def test_auto_update_must_be_an_object(self) -> None:
        self._set_auto_update("yes")
        result = knowledge_ingest.submit(self.root, "T-0001")
        self.assertEqual(result["gate"], "knowledge_config_invalid")
        self.assertIn("knowledge.auto_update 必须是对象", result["errors"][0])

    def test_numeric_timeout_string_is_tolerated(self) -> None:
        normalized = knowledge_ingest.normalize_config(
            {"knowledge": {"auto_update": {"timeout_s": "60"}}}
        )
        self.assertTrue(normalized["ok"], normalized)
        self.assertEqual(normalized["config"]["timeout_s"], 60)

    def test_unsafe_table_id_is_rejected(self) -> None:
        sds = context.load_json(self.req / "sds_design.json", {})
        sds["target_table"]["name"] = "../escape"
        context.write_json(self.req / "sds_design.json", sds)
        built = knowledge_ingest.build_request(self.root, "T-0001")
        self.assertFalse(built["ok"])
        self.assertEqual(built["gate"], "knowledge_unsafe_ident")
        result = knowledge_ingest.submit(self.root, "T-0001")
        self.assertEqual(result["gate"], "knowledge_unsafe_ident")

    def test_sds_target_table_must_be_an_object(self) -> None:
        sds = context.load_json(self.req / "sds_design.json", {})
        sds["target_table"] = "ads_broken"
        context.write_json(self.req / "sds_design.json", sds)
        built = knowledge_ingest.build_request(self.root, "T-0001")
        self.assertFalse(built["ok"])
        self.assertEqual(built["gate"], "knowledge_sds_invalid")

    def test_non_dict_mappings_are_skipped_with_warning(self) -> None:
        sds = context.load_json(self.req / "sds_design.json", {})
        good = sds["field_mappings"][0]
        sds["field_mappings"] = ["not-an-object", good]
        context.write_json(self.req / "sds_design.json", sds)
        built = knowledge_ingest.build_request(self.root, "T-0001")
        self.assertTrue(built["ok"], built)
        request = built["request"]
        self.assertEqual(len(request["columns"]), 1)
        self.assertTrue(request["mapping_warnings"])

    def test_non_dict_sql_evidence_does_not_crash(self) -> None:
        sds = context.load_json(self.req / "sds_design.json", {})
        sds["field_mappings"][0]["sql_evidence"] = "not-an-object"
        context.write_json(self.req / "sds_design.json", sds)
        built = knowledge_ingest.build_request(self.root, "T-0001")
        self.assertTrue(built["ok"], built)
        self.assertIn("join_match_rate=None", built["request"]["calibers"][0])

    def test_request_carries_absolute_roots_the_kb_plugin_resolves(self) -> None:
        """artifact_root / consumer_root 是功能字段，必须仍是绝对路径。

        实测教训：把它们改成项目相对路径会让知识库 ingest 找不到任务产物、
        消费索引同步到错误位置——所以这条固定住契约，而不是"看起来更干净"。
        """
        built = knowledge_ingest.build_request(self.root, "T-0001")
        request = built["request"]
        self.assertEqual(Path(request["consumer_root"]), self.root)
        self.assertEqual(Path(request["artifact_root"]), self.req)

    def test_platform_doctor_surfaces_knowledge_config_error(self) -> None:
        self._set_auto_update({"enabled": True, "timeout_s": -5})
        proc = subprocess.run(
            [sys.executable, str(ENGINE / "ddp.py"), "--root", str(self.root), "platform", "doctor"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout.strip())
        self.assertFalse(payload["knowledge_auto_update"]["ok"])
        self.assertTrue(
            any("timeout_s" in item for item in payload["knowledge_auto_update"]["errors"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
