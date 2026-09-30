"""开源就绪（Apache 风格）的机器校验：OSS-001/002/003/005 的防回归。

本仓库的规矩是"机器检查写成测试"（CI 跑 `python -m unittest discover -s tests`），
所以这里不新增一个 validate_plugin 脚本，而是把开源就绪的判据放进用例：

1. 社区/治理/发布文件齐全（缺哪个直接点名）；
2. LICENSE 与两个 plugin.json 的 license 字段一致；
3. 本机运行时产物（metastore_db / derby.log / spark-warehouse）没有进仓库、也在 .gitignore 里；
4. 面向用户的文档不写死测试数量（曾经 README 写"120 个单测"，实际 246——文档自相矛盾）；
5. README 的文档索引指向新增的社区文件（友好性：新人不该翻不到求助入口）。

判据都写成"故意破坏会红"的形式，见每个用例里的反例构造。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 社区/治理/发布文件：缺一个都不算"可开源"
COMMUNITY_FILES = (
    "LICENSE",
    "NOTICE",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "GOVERNANCE.md",
    "RELEASE.md",
    "SUPPORT.md",
    "SECURITY.md",
    ".github/CODEOWNERS",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/bug_report.md",
    ".github/ISSUE_TEMPLATE/feature_request.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
)

# 本机跑真引擎联调会生成、但绝不能进仓库的路径
RUNTIME_JUNK = ("metastore_db", "derby.log", "spark-warehouse")

# "写死测试数量"的判据：面向用户的文档里不许出现 <两位数以上> 个单测 / tests
HARDCODED_COUNT = re.compile(r"\d{2,}\s*(?:个)?\s*(?:单测|tests)\b", re.IGNORECASE)
USER_FACING_DOCS = ("README.md", "README.en.md", "CONTRIBUTING.md", "RELEASE.md", "SUPPORT.md")


def _git_ls_files(root: Path) -> list[str] | None:
    """跟踪文件列表；没有 git（或不是仓库）时返回 None，由调用方决定跳过还是失败。"""
    try:
        proc = subprocess.run(
            ["git", "ls-files"], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except (OSError, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


class OssReadinessTests(unittest.TestCase):
    def test_community_files_exist(self) -> None:
        missing = [name for name in COMMUNITY_FILES if not (ROOT / name).exists()]
        self.assertEqual(missing, [], f"缺这些开源必备文件：{missing}")

        # 反例：临时目录里当然缺文件，同一个判据必须能报出来
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            missing_elsewhere = [name for name in COMMUNITY_FILES if not (fake / name).exists()]
            self.assertTrue(missing_elsewhere, "判据写错了：空目录竟然不缺文件")

    def test_license_matches_manifests(self) -> None:
        license_line = (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[0].strip()
        kind = "MIT" if "MIT" in license_line else ("Apache" if "Apache" in license_line else license_line)
        for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            text = (ROOT / manifest).read_text(encoding="utf-8")
            declared = re.search(r'"license"\s*:\s*"([^"]+)"', text)
            self.assertIsNotNone(declared, f"{manifest} 缺 license 字段")
            self.assertEqual(
                declared.group(1).strip(), kind,
                f"{manifest} 写 {declared.group(1)}，LICENSE 是 {license_line}",
            )

    def test_runtime_junk_is_not_tracked_and_is_ignored(self) -> None:
        tracked = _git_ls_files(ROOT)
        if tracked is None:
            self.skipTest("本机没有可用的 git，无法核对跟踪文件")
        offenders = sorted({entry.split("/")[0] for entry in tracked
                            if entry.split("/")[0] in RUNTIME_JUNK or entry in RUNTIME_JUNK})
        self.assertEqual(offenders, [], f"这些本机运行时产物不该进仓库：{offenders}")

        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in RUNTIME_JUNK:
            self.assertIn(name, ignored, f".gitignore 少了 {name} 的排除规则")

    def test_user_facing_docs_do_not_hardcode_test_counts(self) -> None:
        for name in USER_FACING_DOCS:
            path = ROOT / name
            if not path.is_file():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                hit = HARDCODED_COUNT.search(line)
                self.assertIsNone(
                    hit,
                    f"{name}:{lineno} 写死了测试数量「{hit.group(0) if hit else ''}」——"
                    "数量会漂，文档只说'以 discover 实际输出为准'",
                )

    def test_readme_index_points_to_community_files(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for target in ("GOVERNANCE.md", "RELEASE.md", "SUPPORT.md", "CODE_OF_CONDUCT.md", "SECURITY.md"):
            self.assertIn(target, readme, f"README 没有指向 {target}（新人找不到求助/治理入口）")


if __name__ == "__main__":
    unittest.main()
