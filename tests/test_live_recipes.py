"""tools/live 的入库纪律（CLOSE-011）：脚本必须能跑、不带本机路径、且在 README 里有索引。

这些脚本来自真跑现场（.tmp），平移入库时最容易出的两个问题：
① 把本机绝对路径（/mnt/c/Users/... 或 /home/<name>）带进来；
② 行尾是 CRLF，bash 直接语法错（2026-10-07 平移时真的踩到）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
LIVE = ROOT_REPO / "tools" / "live"

# 只允许"当前用户家目录"以 $HOME 形式出现；出现具体用户名/盘符路径就是泄漏
ABSOLUTE_LEAK = re.compile(r"/mnt/c/Users/|C:\\Users|/home/[a-z][a-z0-9_-]+/", re.IGNORECASE)


class LiveRecipeTests(unittest.TestCase):
    def _shells(self) -> list[Path]:
        return sorted(LIVE.glob("*.sh"))

    def test_scripts_exist_and_are_indexed(self) -> None:
        readme = (LIVE / "README.md").read_text(encoding="utf-8")
        shells = self._shells()
        self.assertGreaterEqual(len(shells), 10, "tools/live 下的驱动脚本太少，索引可能失效")
        for path in shells:
            if path.name.startswith("_"):
                continue  # 下划线开头是内部片段（公共库/分步片段），不要求单列
            self.assertIn(path.name, readme, f"{path.name} 没写进 tools/live/README.md 的索引")

    def test_no_local_absolute_paths(self) -> None:
        for path in sorted(LIVE.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hit = ABSOLUTE_LEAK.search(text)
            self.assertIsNone(hit, f"{path.relative_to(ROOT_REPO)} 里带了本机绝对路径：{hit.group(0) if hit else ''}")

    def test_shell_syntax_and_line_endings(self) -> None:
        # Windows 上的 bash（Git Bash/WSL）拿不到 `C:\...` 形式的路径，语法检查只在 POSIX 上做；
        # 行尾检查两边都做（CRLF 是 bash 语法错的根因）。
        bash = None if os.name == "nt" else shutil.which("bash")
        for path in self._shells():
            raw = path.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"{path.name} 是 CRLF 行尾，bash 会语法错")
            if bash is None:
                continue  # 没有 bash（例如 Windows 本机）时只查行尾
            proc = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"{path.name} 语法检查未过：{proc.stderr.strip()[:200]}")


if __name__ == "__main__":
    unittest.main()
