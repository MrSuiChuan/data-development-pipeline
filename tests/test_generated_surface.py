"""生成物必须与手写源一致。

`tools/build_codex_surface.py` 从 `commands/*.md` 生成 12 个文件（Codex 清单、共用
hooks、9 个 `ddp-*` 入口技能、AGENTS 模板）。改完 command 忘了重跑生成器，Codex
面就会悄悄落后于 Claude 面——两个宿主跑出不同的流程，而且没有任何报错。

这个仓库的全部主张是"不信任自述、用机器证据兜底"，所以构建这一步也不能靠"记得
重跑"。测试直接把生成器输出截在内存里，与仓库文件逐字节比对，**不写盘**。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Dict

ROOT_REPO = Path(__file__).resolve().parent.parent

# 生成器声明要写的文件名（不含目录），少写一个就说明生成逻辑退化了
EXPECTED_COUNT = 12
EXPECTED_SKILLS = 9


def capture_generator_output() -> Dict[str, str]:
    spec = importlib.util.spec_from_file_location(
        "ddp_build_codex_surface", ROOT_REPO / "tools" / "build_codex_surface.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    captured: Dict[str, str] = {}

    def spy(path: Path, text: str) -> None:
        captured[path.relative_to(module.ROOT).as_posix()] = text

    module.write = spy  # 只截获，不落盘
    module.build_codex_manifest()
    module.build_codex_hooks()
    module.build_command_skills()
    module.build_agents_template()
    return captured


class GeneratedSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generated = capture_generator_output()

    def test_generator_still_covers_every_surface_file(self) -> None:
        """防止"少生成几个文件"也能通过后面的比对。"""
        self.assertEqual(len(self.generated), EXPECTED_COUNT, sorted(self.generated))
        skills = [p for p in self.generated if p.startswith("skills/")]
        self.assertEqual(len(skills), EXPECTED_SKILLS, skills)
        for required in (
            ".codex-plugin/plugin.json",
            "hooks/claude-codex-hooks.json",
            "engine/templates/AGENTS.ddp.md",
        ):
            self.assertIn(required, self.generated)

    def test_generated_files_match_the_repo_byte_for_byte(self) -> None:
        drift = []
        for rel, want in sorted(self.generated.items()):
            real = ROOT_REPO / rel
            if not real.is_file():
                drift.append(f"{rel}: 生成器会写这个文件，但仓库里没有")
                continue
            have = real.read_text(encoding="utf-8")
            if have != want:
                drift.append(
                    f"{rel}: 与生成器输出不一致"
                    f"（仓库 {len(have)} 字符 / 生成 {len(want)} 字符）"
                )
        self.assertEqual(
            drift,
            [],
            "生成物已漂移，重跑 `py -3 tools/build_codex_surface.py` 后重新提交：\n  "
            + "\n  ".join(drift),
        )


if __name__ == "__main__":
    unittest.main()
