"""SECURITY.md 里「已知边界」的可执行版本。

把文档里那些声明**真跑一遍**：哪些绕过已经被拦住了（回归保护），哪些还开着
（已知上限）。文档和这份测试必须一起改——写完时逐条实测过，当时发现
SECURITY.md 的清单是**过期**的：四条"已知绕过"里有三条其实早就拦住了。

来源：SECURITY.md > Known limits / non-goals 第 1 条。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_REPO / "engine"))

from core import context, gates  # noqa: E402

# 文档曾把它们列为"可绕过"，实测**已经被拦住**——留在这里防止回退。
BLOCKED_BYPASSES = [
    ("PowerShell 间接调用 & $c", '$c = "Remove-Item .data-dev/runtime/x.json"; & $c'),
    ("环境变量拼路径", '$t = ".data-dev/runtime/x.json"; Set-Content -Path $t -Value "{}"'),
    ("变量拼接出路径", '$a = ".data-dev"; $b = "/runtime/x.json"; Set-Content -Path "$a$b" -Value "{}"'),
    ("从别的目录写回受保护位置", 'cd /tmp; Set-Content -Path "C:\\proj\\.data-dev\\runtime\\x.json" -Value "{}"'),
    ("python 写文件", "py -3 -c \"import pathlib; pathlib.Path('.data-dev/runtime/x.json').write_text('{}')\""),
    ("先 cd 进受保护目录再写", 'cd .data-dev/runtime; Set-Content -Path x.json -Value "{}"'),
    ("管道写出", "echo {} | Out-File .data-dev/runtime/x.json"),
    ("整条命令放进变量再执行", '$e = "Remove-Item .data-dev/runtime/x.json"; iex $e'),
]

# 真正还开着的一道口子。
STILL_OPEN = [
    ("把写操作放进脚本文件再执行", "py -3 write_runtime.py"),
]


class KnownLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # 造一个已初始化的项目，避免 setup_guard 提前把命令拦掉、掩盖真正的结论
        context.ensure_app_dir(self.root, mode="mock")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_documented_bypasses_are_actually_blocked(self) -> None:
        for label, command in BLOCKED_BYPASSES:
            decision = gates.decide_cli_command(command, self.root)
            self.assertTrue(
                decision.blocked(),
                f"{label}：现在放行了。SECURITY.md 说它已被拦住——要么修门，要么改文档，"
                f"两者必须一致。（decision={decision}）",
            )

    def test_the_one_bypass_that_is_still_open(self) -> None:
        """hook 只看得见宿主暴露的那条命令，看不见脚本文件的内容。

        这条断言的是**当前上限，不是目标**。哪天有人让 hook 也去看脚本内容，
        这条会红——那时请删掉它，并把 SECURITY.md 的 Known limits 同步收紧。
        """
        for label, command in STILL_OPEN:
            decision = gates.decide_cli_command(command, self.root)
            self.assertFalse(
                decision.blocked(),
                f"{label}：现在被拦住了。这是好事——请删掉这条用例并把 SECURITY.md 的"
                f"已知边界同步收紧。（decision={decision}）",
            )


if __name__ == "__main__":
    unittest.main()
