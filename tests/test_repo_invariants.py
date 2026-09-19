"""仓库级不变量：没人守的元数据，迟早和事实分家。

两个检查都是"读文件、比对"，不起子进程、不写盘，跑完不到一秒：

  * 版本号：Claude 清单是唯一来源，Codex 清单的基础版本与 CHANGELOG 的最新已发布
    小节都必须由它派生（DEVEX-002）；
  * 引擎完整性清单：`engine/.engine-manifest.json` 必须与 engine/ 当前内容一致，
    否则每个贡献者 `/setup` 之后的 engine_integrity 自检都会失败（DEVEX-003）。

注意两个测试都**只读不写**：写成"跑生成器再比对"会自己把漂移修好，然后永远通过。
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path
from typing import Any

ROOT_REPO = Path(__file__).resolve().parent.parent
CLAUDE_MANIFEST = ROOT_REPO / ".claude-plugin" / "plugin.json"
CODEX_MANIFEST = ROOT_REPO / ".codex-plugin" / "plugin.json"
CHANGELOG = ROOT_REPO / "CHANGELOG.md"
ENGINE_MANIFEST = ROOT_REPO / "engine" / ".engine-manifest.json"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest_version(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8")).get("version") or "").strip()


def changelog_released_version() -> str:
    """CHANGELOG 里最新的**已发布**版本，跳过 `[Unreleased]`。"""
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^## \[([^\]]+)\]", line)
        if not match:
            continue
        tag = match.group(1).strip()
        if tag.lower() == "unreleased":
            continue
        return tag
    raise AssertionError("CHANGELOG 里找不到已发布版本小节")


class VersionConsistencyTests(unittest.TestCase):
    def test_base_version_agrees_across_manifests_and_changelog(self) -> None:
        claude = manifest_version(CLAUDE_MANIFEST)
        codex_base = manifest_version(CODEX_MANIFEST).split("+", 1)[0]
        changelog = changelog_released_version()
        self.assertEqual(
            {claude, codex_base, changelog},
            {claude},
            "版本号不一致："
            f".claude-plugin={claude} / .codex-plugin 基础版本={codex_base} / "
            f"CHANGELOG 最新已发布={changelog}。"
            "基础版本以 .claude-plugin/plugin.json 为准，改完请重跑 "
            "tools/build_codex_surface.py。",
        )

    def test_codex_suffix_is_the_codex_cachebuster(self) -> None:
        """`+` 后面只允许 codex 自己的 cachebuster，避免手工造出别的后缀。"""
        codex = manifest_version(CODEX_MANIFEST)
        if "+" in codex:
            suffix = codex.split("+", 1)[1]
            self.assertTrue(
                suffix.startswith("codex."), f"未知版本后缀：{suffix}（用 tools/reinstall.py 生成）"
            )


class EngineManifestTests(unittest.TestCase):
    def test_manifest_matches_current_engine_files(self) -> None:
        tool = load_module("ddp_generate_engine_manifest", ROOT_REPO / "tools" / "generate_engine_manifest.py")
        expected = tool.build_manifest()
        recorded = json.loads(ENGINE_MANIFEST.read_text(encoding="utf-8")).get("files") or {}

        only_recorded = sorted(set(recorded) - set(expected))
        only_present = sorted(set(expected) - set(recorded))
        changed = sorted(k for k in set(recorded) & set(expected) if recorded[k] != expected[k])

        problems = []
        if only_recorded:
            problems.append(f"清单里有、engine/ 里没有: {only_recorded}")
        if only_present:
            problems.append(f"engine/ 里有、清单里没有: {only_present}")
        if changed:
            problems.append(f"内容变了但清单没更新: {changed}")
        self.assertEqual(
            problems,
            [],
            "engine/.engine-manifest.json 已过期，"
            "跑 `py -3 tools/generate_engine_manifest.py` 后重新提交：\n  " + "\n  ".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
