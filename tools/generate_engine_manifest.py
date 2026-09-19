#!/usr/bin/env python3
"""Regenerate engine/.engine-manifest.json after any engine/ code change.

Run from the repository root:

    python tools/generate_engine_manifest.py

The manifest is shipped inside engine/ so /setup copies carry a sha256 baseline
used by ``ddp.py`` engine-integrity self-checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(ENGINE))

from core import context  # noqa: E402  （复用完整性自检用的同一个哈希函数）


def build_manifest() -> dict:
    """按 engine/ 当前内容算出清单（纯函数：不写盘，便于测试比对）。"""
    manifest: dict[str, str] = {}
    for path in sorted(ENGINE.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        if path.name == ".engine-manifest.json":
            continue
        # 用 context.sha256_file（行尾归一到 LF）而不是自己算字节哈希——
        # 两处规则必须完全一致，否则跨平台 clone 时清单必然对不上。
        manifest[path.relative_to(ENGINE).as_posix()] = context.sha256_file(path)
    return manifest


def main() -> int:
    manifest = build_manifest()
    (ENGINE / ".engine-manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"manifest written: {len(manifest)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
