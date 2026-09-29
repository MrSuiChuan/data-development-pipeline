"""live_probe 的最小自检：只验"命令拼装"，不碰真组件（LIVE-010）。"""

from __future__ import annotations

import io
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_REPO / "tools"))

import live_probe  # noqa: E402


class LiveProbeTests(unittest.TestCase):
    def test_dry_run_prints_engine_universal_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            spec = project / "spec.json"
            spec.write_text("{}", encoding="utf-8")
            buffer = io.StringIO()
            argv = ["live_probe.py", "--project", str(project), "--table", "paimon.db.ods_x",
                    "--spec", str(spec), "--dry-run", "--stop-container", "ddp-x"]
            with contextlib.redirect_stdout(buffer):
                original = sys.argv
                sys.argv = argv
                try:
                    code = live_probe.main()
                finally:
                    sys.argv = original
            out = buffer.getvalue()
            self.assertEqual(code, 0)
            for needle in ("platform doctor", "ddl render", "run-sql", "--save-evidence",
                           "evidence verify", "docker stop ddp-x"):
                self.assertIn(needle, out, needle)

    def test_table_prefix_does_not_leak_into_file_name(self) -> None:
        """带 catalog/db 前缀的表名只用末段做文件名（P3 门比的是文件 stem）。"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            spec = project / "spec.json"
            spec.write_text("{}", encoding="utf-8")
            steps = live_probe.build_steps(project, spec, "ods_x", "INSERT", "SELECT 1")
            written = [s["write"]["path"] for s in steps if "write" in s]
            self.assertTrue(written)
            normalized = written[0].replace("\\", "/")
            self.assertTrue(normalized.endswith("etl/ods_x.sql"), normalized)


if __name__ == "__main__":
    unittest.main()
