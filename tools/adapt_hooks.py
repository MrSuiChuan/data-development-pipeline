#!/usr/bin/env python3
"""Adapt hooks/*.json to the Python launcher that exists on this machine.

Repositories default to ``py -3`` (the Windows launcher). macOS/Linux have no
``py``, so the hook command fails, the host treats that as fail-open, and every
hard gate silently stops working. Run this once after installing on POSIX:

    python3 tools/adapt_hooks.py            # switch to this platform's launcher
    python3 tools/adapt_hooks.py --check    # report only, change nothing

``--check`` exits non-zero when a hooks file references a launcher that is not
on PATH here — that is exactly the "installed but not enforcing" state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, List

ROOT = Path(__file__).resolve().parent.parent
HOOKS_FILES = (
    ROOT / "hooks" / "hooks.json",                 # Claude Code
    ROOT / "hooks" / "claude-codex-hooks.json",    # Claude Code + Codex 共用
)
# 只替换开头那个启动器词，命令其余部分（路径、引号）原样保留
_LAUNCHER_RE = re.compile(r"^\s*(py\s+-3|python3|python)\s+", re.IGNORECASE)
DEFAULT_LAUNCHER = "py -3" if os.name == "nt" else "python3"


def iter_commands(data: Any) -> List[str]:
    out: List[str] = []
    for groups in (data.get("hooks") or {}).values():
        for group in groups or []:
            for entry in (group.get("hooks") or []):
                command = entry.get("command")
                if isinstance(command, str):
                    out.append(command)
    return out


def iter_entries(data: Any):
    for groups in (data.get("hooks") or {}).values():
        for group in groups or []:
            for entry in (group.get("hooks") or []):
                if isinstance(entry, dict):
                    yield entry


def launcher_of(command: str) -> str:
    match = _LAUNCHER_RE.match(command)
    return re.sub(r"\s+", " ", match.group(1).lower()) if match else ""


def rewrite(command: str, launcher: str) -> str:
    if _LAUNCHER_RE.match(command):
        return _LAUNCHER_RE.sub(launcher + " ", command, count=1)
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description="adapt hooks/*.json to this platform")
    parser.add_argument("--check", action="store_true", help="只体检，不改文件")
    args = parser.parse_args()

    print(f"platform: {'windows' if os.name == 'nt' else 'posix'} | default launcher: {DEFAULT_LAUNCHER}")
    for candidate in ("py", "python3", "python"):
        print(f"  which {candidate:<8} -> {shutil.which(candidate) or 'NOT FOUND'}")

    missing: List[str] = []
    changed = False
    for hooks_path in HOOKS_FILES:
        if not hooks_path.is_file():
            continue
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        launchers = {launcher_of(c) for c in iter_commands(data)}

        if args.check:
            print(f"check {hooks_path.name}: launchers={sorted(launchers) or ['(none)']}")
            for launcher in launchers:
                if launcher and not shutil.which(launcher.split()[0]):
                    missing.append(f"{hooks_path.name}: {launcher}")
            continue

        touched = False
        for entry in iter_entries(data):
            updated = rewrite(str(entry.get("command") or ""), DEFAULT_LAUNCHER)
            if updated != entry.get("command"):
                entry["command"] = updated
                touched = True
        if touched:
            hooks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"switched {hooks_path.name} to {DEFAULT_LAUNCHER}")
            changed = True
        else:
            print(f"no change: {hooks_path.name} already uses {DEFAULT_LAUNCHER}")

    if args.check:
        if missing:
            print()
            print("启动器不在本机 PATH 上，hook 不会生效（宿主会 fail-open 放行）：")
            for item in missing:
                print(f"  - {item}")
            print("修法：python3 tools/adapt_hooks.py")
            return 1
        print()
        print("hook 启动器在本机可用。")
        return 0

    print("done" if changed else "nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
