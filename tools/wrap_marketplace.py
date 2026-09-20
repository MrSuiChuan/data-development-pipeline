#!/usr/bin/env python3
"""Package this plugin folder into a Claude Code marketplace repo.

Usage:
    python tools/wrap_marketplace.py <target-repo-root> [--name my-marketplace]

Creates:
    <target-repo-root>/
      .claude-plugin/marketplace.json
      plugins/data-development-plugin/   (copy of this plugin)

Then install from Claude Code:
    claude plugin marketplace add <target-repo-root>
    claude plugin install data-development-plugin
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Windows 上 stdout 被重定向时是 cp1252/cp936，中文进度行会直接 UnicodeEncodeError。
# 交互式控制台走 PEP 528 的宽字符 API，本来就支持中文，这里的 reconfigure 是空操作。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PLUGIN_NAME = "data-development-plugin"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="target marketplace repo root")
    parser.add_argument("--name", default="personal", help="marketplace name (default: personal)")
    args = parser.parse_args()
    target = Path(args.target).resolve()
    plugin_dst = target / "plugins" / PLUGIN_NAME
    if plugin_dst.exists():
        print(f"目标已存在，先移除: {plugin_dst}", file=sys.stderr)
        shutil.rmtree(plugin_dst)
    shutil.copytree(
        PLUGIN_ROOT,
        plugin_dst,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".data-dev"),
    )
    manifest_dir = target / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    marketplace = {
        "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
        "name": args.name,
        "description": "通用离线数据任务研发流水线插件（data-development-plugin 的本地 marketplace 包装）",
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "description": "六阶段状态机 + Hook 硬门的离线数据任务研发插件",
                "author": {"name": "data-development-plugin maintainers"},
                "source": f"./plugins/{PLUGIN_NAME}",
                "category": "development",
            }
        ],
    }
    (manifest_dir / "marketplace.json").write_text(
        json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "marketplace_root": str(target)}, ensure_ascii=False, indent=2))
    print(f"\n安装：\n  claude plugin marketplace add {target}\n  claude plugin install {PLUGIN_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
