"""SDS 终版评论的"最新"判定（2026-10-07 真 tracker 上踩出来的）。

真平台按时间序返回评论；同一个版本号可能被贴多次（重跑 SDS 再贴一条 v1）。
`max()` 在平局时取**第一个**最大元素——于是"验最新"变成了"验最旧"，闸门会误判。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_REPO / "engine"))

from core import spec  # noqa: E402

GOOD = "# 数据概要设计（T-0001）\n\n## 一、需求与结论\n\n```json\n{\"ticket_id\": \"T-0001\", \"confirmed\": true}\n```\n\n确认行: [已确认] 数据概要设计 v1\n"


class LatestCommentTests(unittest.TestCase):
    def test_same_version_takes_the_last_one(self) -> None:
        comments = [
            {"anchor": "数据概要设计 v1", "content_md": "旧的一条，没有确认行"},
            {"anchor": "数据概要设计 v1", "content_md": GOOD},
        ]
        result = spec.verify_latest_comment(comments)
        self.assertIsNotNone(result)
        self.assertIs(result["comment"], comments[-1], "同版本时应取最后一条（最新的）")
        self.assertTrue(result["verified"], "最后一条是合格终版，应当 verified=True")

    def test_higher_version_wins(self) -> None:
        comments = [
            {"anchor": "数据概要设计 v1", "content_md": GOOD},
            {"anchor": "数据概要设计 v2", "content_md": "v2 还没写完"},
        ]
        result = spec.verify_latest_comment(comments)
        self.assertIs(result["comment"], comments[-1])
        self.assertFalse(result["verified"], "v2 缺确认行/json 块时不能算通过")

    def test_missing_confirmation_line_is_not_verified(self) -> None:
        comments = [{"anchor": "数据概要设计 v1", "content_md": "## 一、需求与结论\n\n```json\n{}\n```\n"}]
        self.assertFalse(spec.verify_latest_comment(comments)["verified"])


if __name__ == "__main__":
    unittest.main()
