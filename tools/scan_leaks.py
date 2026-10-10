#!/usr/bin/env python3
"""发布面泄漏扫描：内网地址与本机绝对路径。

为什么加这一条：2026-10-09 第一次把仓库推到公开仓库时，CI 的脱敏扫描**没有拦下来** ——
它只查 6 个中文词和两种密钥格式，而漏出去的恰恰是内网服务地址、私有网段和带用户名的
本机路径。那次的代价是重写全历史 + 强推，所以才把这两类补进 CI。

与 CI 里那两条 ``rg`` 的分工：那两条查"业务/来源敏感词"，这条查"环境指纹"。

修法优先级：优先把原文改成占位符（``<LAN-IP>``、``~`` 之类），而不是往豁免清单里加文件 ——
豁免清单只用于**扫描器自己**、**定义规则的测试**这类"本来就该写着这些模式"的地方。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Windows 控制台默认 GBK，直接 print 中文会抛 UnicodeEncodeError（本地实测）；
# CI 是 utf-8 不受影响，这里只是让本地也能正常跑。
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - 老版本 Python 或非标准 stdout
    pass

# 私有网段：10/8、192.168/16、172.16/12。回环与通配不算（配置里本来就要写）。
PRIVATE_IP = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)

# 本机绝对路径：Windows 用户目录、WSL 挂载点、Linux 家目录。
LOCAL_PATH = re.compile(
    r"[A-Za-z]:\\Users\\"          # C:\Users\...
    r"|/mnt/[a-z]/Users/"          # /mnt/c/Users/...
    r"|/home/(?!hive/)[a-z]"       # /home/<user>；/home/hive 是 Hive 自己的目录，不算个人路径
)

# 本来就该写着这些模式的文件：
ALLOWLIST = {
    "tools/scan_leaks.py",        # 本扫描器
    "tools/publish_public.sh",    # 脱敏同步工具，规则就写在里面
    "tests/test_live_recipes.py",  # 定义了"live 脚本不得含绝对路径"的规则
}
# 内部工作台账不进公开镜像，里面的原始环境细节是刻意保留的
ALLOWLIST_PREFIXES = (".awr/", ".github/")


def tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [REPO / line for line in out.splitlines() if line.strip()]


def main() -> int:
    findings: list[tuple[str, int, str, str]] = []
    for path in tracked_text_files():
        rel = path.relative_to(REPO).as_posix()
        if rel in ALLOWLIST or rel.startswith(ALLOWLIST_PREFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in (("内网地址", PRIVATE_IP), ("本机路径", LOCAL_PATH)):
                m = pattern.search(line)
                if m:
                    findings.append((rel, lineno, label, m.group(0)))

    if not findings:
        print("OK 发布面干净：没有内网地址，也没有带用户名的本机绝对路径")
        return 0

    print(f"发现 {len(findings)} 处：")
    for rel, lineno, label, hit in findings:
        print(f"  {rel}:{lineno}  [{label}] {hit}")
    print()
    print("处置：优先改成占位符（<LAN-IP> / ~）；确属规则或工具本身的，才加进 ALLOWLIST。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
