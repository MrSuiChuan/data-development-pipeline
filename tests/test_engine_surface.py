"""面向模型的文档面：引擎/方言/格式的能力必须在 skills 与 commands 里出现（COMPAT-012）。

这个仓库没有 evals 跑分器——行为回归靠三层机器检查：单测（tests/）、生成物一致性
（tools/build_codex_surface.py --check）、仓库不变量（test_repo_invariants.py）。
本文件补的是第四层里最容易被漏掉的一处：**模型读到的文档**。
引擎能力如果只写在引擎代码里、没写进 skill/command，模型就不会去用——
那等于"支持了主流组件但没人知道"。
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent

# 这两份是 P3 研发阶段模型真正读的文档（commands 是 Claude 面，skills 是方法论）
SURFACES = (
    "commands/start.md",
    "skills/data-develop/SKILL.md",
)


class EngineSurfaceTests(unittest.TestCase):
    def _read(self, rel: str) -> str:
        return (ROOT_REPO / rel).read_text(encoding="utf-8")

    def test_p3_surface_points_to_engine_dimensions(self) -> None:
        for rel in SURFACES:
            text = self._read(rel)
            for needle, why in (
                ("platform doctor", "没提配置体检：配错 kind/format 会被当成环境问题排查"),
                ("--dialect", "没提方言检查：模型不会按引擎跑规则"),
                ("--format", "没提湖表格式检查"),
                ("ddl render", "没提按引擎出建表模板"),
            ):
                self.assertIn(needle, text, f"{rel} 缺「{why}」（{needle}）")

    def test_surface_does_not_promise_silent_fallback(self) -> None:
        """文档里不许出现"未登记 kind 会自动回退"这类描述——那是被修掉的静默降级。"""
        for rel in SURFACES:
            text = self._read(rel)
            for forbidden in ("自动回退", "静默回退", "回退到 CLI"):
                self.assertNotIn(forbidden, text, f"{rel} 描述了不存在的回退行为")

    def test_engine_matrix_is_linked_from_surface(self) -> None:
        """矩阵是引擎能力的落点，至少要被文档索引指到一次（README 或命令面）。"""
        linked = any("docs/engines/README.md" in self._read(rel) for rel in ("README.md", *SURFACES))
        self.assertTrue(linked, "没有任何面向人的入口指向 docs/engines/README.md")


if __name__ == "__main__":
    unittest.main()
