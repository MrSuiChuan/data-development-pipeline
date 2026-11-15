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


if __name__ == "__main__":
    unittest.main(verbosity=2)
