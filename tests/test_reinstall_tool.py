"""Cachebuster / marketplace 路径解析：这两处错了会静默装一个旧副本。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
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


class DropMigratedSkillsTests(unittest.TestCase):
    """宿主生成的迁移技能副本必须被清掉：它和自带的 ddp-* 技能指的是同一批 commands。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self._original = reinstall.CACHE_ROOT
        reinstall.CACHE_ROOT = self.cache

    def tearDown(self) -> None:
        reinstall.CACHE_ROOT = self._original
        self._tmp.cleanup()

    def _make_install(self, marketplace: str, version: str, with_migrated: bool) -> Path:
        root = self.cache / marketplace / "data-development-plugin" / version
        (root / "skills" / "ddp-advance").mkdir(parents=True)
        if with_migrated:
            migrated = root / reinstall.MIGRATED_SKILLS_DIR / "source-command-advance"
            migrated.mkdir(parents=True)
            (migrated / "SKILL.md").write_text("---\nname: source-command-advance\n---\n", encoding="utf-8")
        return root

    def test_removes_the_migrated_copy_and_keeps_the_native_skills(self) -> None:
        root = self._make_install("personal", "1.3.0+codex.tok", with_migrated=True)
        removed = reinstall.drop_migrated_skills("1.3.0+codex.tok", "data-development-plugin")
        self.assertEqual(len(removed), 1, removed)
        self.assertFalse((root / reinstall.MIGRATED_SKILLS_DIR).exists())
        self.assertTrue((root / "skills" / "ddp-advance").is_dir(), "自带技能不能被一起删掉")

    def test_leaves_other_versions_alone(self) -> None:
        self._make_install("personal", "1.3.0+codex.old", with_migrated=True)
        current = self._make_install("personal", "1.3.0+codex.tok", with_migrated=True)
        reinstall.drop_migrated_skills("1.3.0+codex.tok", "data-development-plugin")
        self.assertFalse((current / reinstall.MIGRATED_SKILLS_DIR).exists())
        self.assertTrue(
            (self.cache / "personal" / "data-development-plugin" / "1.3.0+codex.old" / reinstall.MIGRATED_SKILLS_DIR).is_dir(),
            "只清本次装的那一版",
        )


if __name__ == "__main__":
    unittest.main()
