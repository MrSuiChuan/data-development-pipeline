"""Cachebuster / marketplace 路径解析：这两处错了会静默装一个旧副本。"""

from __future__ import annotations

import importlib.util
import os
import stat
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


class PruneCacheArtifactsTests(unittest.TestCase):
    """宿主安装拷的是整棵工作树，本机产物得从缓存副本里清掉——但只清缓存，不动源码仓库。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self._original = reinstall.CACHE_ROOT
        reinstall.CACHE_ROOT = self.cache
        root = self.cache / "personal" / "data-development-plugin" / "1.3.0+codex.tok"
        (root / ".tmp").mkdir(parents=True)
        (root / ".tmp" / "probe.bin").write_bytes(b"x" * 2048)
        (root / "logs").mkdir(parents=True)
        (root / "logs" / "run.log").write_text("log", encoding="utf-8")
        (root / "derby.log").write_text("derby", encoding="utf-8")
        (root / "engine").mkdir(parents=True)
        (root / "engine" / "ddp.py").write_text("# publish surface", encoding="utf-8")
        self.root = root

    def tearDown(self) -> None:
        reinstall.CACHE_ROOT = self._original
        self._tmp.cleanup()

    def test_prunes_local_artifacts_and_keeps_the_publish_surface(self) -> None:
        pruned, freed = reinstall.prune_cache_artifacts("1.3.0+codex.tok", "data-development-plugin")
        self.assertEqual(len(pruned), 3, pruned)  # .tmp + logs + derby.log
        self.assertGreater(freed, 2000)
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse((self.root / "derby.log").exists())
        # 仓库里同名目录不受影响（只有缓存副本被清）
        self.assertTrue((self.root / "engine" / "ddp.py").is_file(), "发布面不能被删")

    def test_prunes_read_only_files_too(self) -> None:
        """实测踩过：.tmp/clonecheck-* 里是嵌套 git 克隆，.git/objects/** 只读，
        rmtree 直接 WinError 5，把整次重装打断。清理必须能处理只读文件。"""
        nested = self.root / ".tmp" / "clonecheck-1" / ".git" / "objects" / "ab"
        nested.mkdir(parents=True)
        loose = nested / "cdef"
        loose.write_bytes(b"object")
        os.chmod(loose, stat.S_IREAD)
        self.assertFalse(os.access(loose, os.W_OK), "前提：文件确实是只读的")

        pruned, _freed = reinstall.prune_cache_artifacts("1.3.0+codex.tok", "data-development-plugin")

        self.assertFalse((self.root / ".tmp").exists(), pruned)


if __name__ == "__main__":
    unittest.main()
