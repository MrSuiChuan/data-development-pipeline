#!/usr/bin/env python3
"""Regenerate engine/.engine-manifest.json after any engine/ code change.

Run from the repository root:

    python tools/generate_engine_manifest.py

The manifest is shipped inside engine/ so /setup copies carry a sha256 baseline
used by ``ddp.py`` engine-integrity self-checks.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "engine"


def main() -> int:
    manifest: dict[str, str] = {}
    for path in sorted(ENGINE.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        if path.name == ".engine-manifest.json":
            continue
        manifest[path.relative_to(ENGINE).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (ENGINE / ".engine-manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"manifest written: {len(manifest)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
