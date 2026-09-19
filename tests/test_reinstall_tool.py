"""Cachebuster / marketplace 路径解析：这两处错了会静默装一个旧副本。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("ddp_reinstall", ROOT_REPO / "tools" / "reinstall.py")
reinstall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reinstall)


class NewVersionTests(unittest.TestCase):
    def test_replaces_suffix_and_keeps_base(self) -> None:
        cases = {
            "1.2.2": "1.2.2+codex.tok",
            "1.2.2+codex.old": "1.2.2+codex.tok",
            "1.2.3-beta.1+codex.prev": "1.2.3-beta.1+codex.tok",
            "dev-build+other-tag": "dev-build+codex.tok",
        }
        for current, expected in cases.items():
            self.assertEqual(reinstall.new_version(current, "tok"), expected, current)


class ResolveSourceTests(unittest.TestCase):
    def test_source_path_is_relative_to_home_not_the_marketplace_file(self) -> None:
        # Codex 的解析口径：~/.agents/plugins/marketplace.json 里的 "./plugins/x"
        # 指向 ~/plugins/x，而不是 ~/.agents/plugins/plugins/x。
        self.assertEqual(
            reinstall.resolve_source("./plugins/data-development-plugin"),
            Path.home() / "plugins" / "data-development-plugin",
        )
        self.assertEqual(reinstall.resolve_source("plugins/x"), Path.home() / "plugins" / "x")


if __name__ == "__main__":
    unittest.main()
