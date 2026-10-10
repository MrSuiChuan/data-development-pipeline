"""发布面不能有环境指纹：内网网段、带用户名的本机绝对路径。

CI 里单独跑了一条 `python tools/scan_leaks.py`；这里再放一条等价用例，好处是本地
`python -m unittest` 就能先拦下来，不必等 CI。

背景：2026-10-09 第一次公开推送时漏出去的正是这两类（内网服务地址、私有网段、本机路径），
而当时的脱敏扫描只查敏感词和密钥格式，拦不住。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
SCANNER = ROOT_REPO / "tools" / "scan_leaks.py"


class LeakScanTests(unittest.TestCase):
    def test_publish_surface_has_no_environment_fingerprints(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCANNER)],
            capture_output=True,
            text=True,
            # 显式指定编码：Windows 下 text=True 会按 GBK 解码，扫描器的中文输出会炸在读取线程里
            encoding="utf-8",
            errors="replace",
            cwd=ROOT_REPO,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"扫描器认为发布面不干净：\n{proc.stdout}\n{proc.stderr}",
        )

    def test_scanner_actually_flags_a_planted_leak(self) -> None:
        """反向用例：扫描器本身要是坏的（永远返回 0），上面那条就形同虚设。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("scan_leaks_under_test", SCANNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        self.assertIsNotNone(module.PRIVATE_IP.search("host: 10.1.2.3 port 9030"))
        self.assertIsNotNone(module.PRIVATE_IP.search("<LAN-IP>"))
        self.assertIsNotNone(module.LOCAL_PATH.search(r"config at C:\Users\someone\x"))
        self.assertIsNotNone(module.LOCAL_PATH.search("/mnt/c/Users/someone/x"))
        # 回环与通配不算泄漏，配置里本来就要写
        self.assertIsNone(module.PRIVATE_IP.search("bind 127.0.0.1"))
        self.assertIsNone(module.PRIVATE_IP.search("listen 0.0.0.0"))
        # Hive 自己的家目录不是"个人路径"
        self.assertIsNone(module.LOCAL_PATH.search("/home/hive/.beeline"))


if __name__ == "__main__":
    unittest.main()
