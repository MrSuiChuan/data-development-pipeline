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
import subprocess
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

# 只是**尝试顺序**，最终按"真的能跑起来"选。
# 两个坑逼出来的：Windows 上 `python3`/`python` 可能指向 Microsoft Store 占位程序
# （shutil.which 找得到，一执行就弹应用商店）；而某些 CI 镜像只有 `python` 没有 `py`。
_LAUNCHER_PREFERENCE = (
    ("py -3", "python3", "python") if os.name == "nt" else ("python3", "python", "py -3")
)


def can_run(launcher: str) -> bool:
    """启动器既要"在 PATH 上"，也要**真能跑起来**——只 which 一下会选中占位程序。"""
    exe = launcher.split()[0]
    if not shutil.which(exe):
        return False
    try:
        proc = subprocess.run(
            [*launcher.split(), "-c", "print(1)"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "1"


def pick_launcher() -> str:
    for candidate in _LAUNCHER_PREFERENCE:
        if can_run(candidate):
            return candidate
    return ""


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

    probes = {candidate: can_run(candidate) for candidate in _LAUNCHER_PREFERENCE}
    chosen = next((c for c in _LAUNCHER_PREFERENCE if probes[c]), "")

    print(f"platform: {'windows' if os.name == 'nt' else 'posix'}")
    for candidate in _LAUNCHER_PREFERENCE:
        print(f"  probe {candidate:<9} -> {'OK' if probes[candidate] else 'NO'}")
    print(f"  chosen: {chosen or '(none)'}")

    if args.check:
        missing: List[str] = []
        for hooks_path in HOOKS_FILES:
            if not hooks_path.is_file():
                continue
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
            launchers = {launcher_of(c) for c in iter_commands(data)}
            print(f"check {hooks_path.name}: launchers={sorted(launchers) or ['(none)']}")
            for launcher in launchers:
                if launcher and not probes.get(launcher, can_run(launcher)):
                    missing.append(f"{hooks_path.name}: {launcher}")
        if missing:
            print()
            print("配置里的启动器在本机跑不起来，hook 不会生效（宿主会 fail-open 放行）：")
            for item in missing:
                print(f"  - {item}")
            print("修法：python3 tools/adapt_hooks.py")
            return 1
        print()
        print("hook 启动器在本机可用。")
        return 0

    if not chosen:
        print()
        print(
            "本机没有能跑起来的 Python 启动器（py -3 / python3 / python 都不可用）："
            "hook 无法工作，硬门不会生效。请先安装 Python 并把启动器放进 PATH。",
            file=sys.stderr,
        )
        return 1

    changed = False
    for hooks_path in HOOKS_FILES:
        if not hooks_path.is_file():
            continue
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        touched = False
        for entry in iter_entries(data):
            updated = rewrite(str(entry.get("command") or ""), chosen)
            if updated != entry.get("command"):
                entry["command"] = updated
                touched = True
        if touched:
            hooks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"switched {hooks_path.name} to {chosen}")
            changed = True
        else:
            print(f"no change: {hooks_path.name} already uses {chosen}")

    print("done" if changed else "nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
