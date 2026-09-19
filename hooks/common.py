"""Shared helpers for the data-development-plugin hooks.

Every hook follows Claude Code's contract:
  * receives one JSON object on stdin;
  * prints one JSON object on stdout;
  * exits 0 (allow / soft message) or 2 (PreToolUse hard block).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)
ENGINE_DIR = PLUGIN_ROOT / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))


def load_payload() -> Dict[str, Any]:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def trace_payload(event: str, payload: Dict[str, Any]) -> None:
    """把原始 hook payload 追加到 ``$DDP_HOOK_TRACE`` 指向的文件（默认关闭）。

    Windows 上排查 Codex hook：``$env:DDP_HOOK_TRACE="C:\\temp\\ddp-hook.jsonl"``
    then run codex / claude — 每个工具的 payload 形状都能看到，便于对齐宿主差异。
    """
    target = os.environ.get("DDP_HOOK_TRACE", "").strip()
    if not target:
        return
    try:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": event, "payload": payload}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def plugin_version() -> str:
    """从插件清单读版本号，避免在 hook 里写死（曾出现消息里写死 v1.1.0）。"""
    for rel in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        path = PLUGIN_ROOT / rel
        if path.is_file():
            try:
                version = json.loads(path.read_text(encoding="utf-8")).get("version")
                if version:
                    return str(version)
            except Exception:
                continue
    return "unknown"


def host_name() -> str:
    """Codex 会注入 PLUGIN_DATA；Claude Code 不会。"""
    return "Codex" if os.environ.get("PLUGIN_DATA") else "Claude Code"


def command_hint(name: str) -> str:
    """同一个动作在两个宿主里的调用写法不同：Codex 是技能名，Claude Code 是斜杠命令。"""
    return f"ddp-{name}" if os.environ.get("PLUGIN_DATA") else f"/{name}"


def project_root(payload: Dict[str, Any]) -> Optional[Path]:
    from core import context

    cwd_value = payload.get("cwd")
    start = Path(cwd_value) if cwd_value and Path(cwd_value).is_dir() else Path.cwd()
    return context.project_root(start)


def emit(obj: Any, exit_code: int = 0) -> None:
    """把 hook 决策写成一行 JSON 给宿主（Claude Code）。

    这里故意用 ``ensure_ascii=True``：Windows 下 ``sys.stdout`` 的编码是 cp936，
    而宿主按 UTF-8 解析 hook 输出，中文会整段变成乱码（实测 Claude Code 里
    ``dev_order_guard`` 的拦截原因显示为乱码）。ASCII 转义后 JSON 在任何编码下
    都等价，由宿主 ``json.loads`` 还原成正确的中文。
    """
    try:
        sys.stdout.write(json.dumps(obj))
        sys.stdout.flush()
    except Exception:
        pass
    sys.exit(exit_code)


def fail_open(message: str) -> None:
    """A hook bug must never lock the user out (mirrors fail-open convention)."""
    emit({"systemMessage": f"[data-development-plugin] hook 内部错误（fail-open）: {message}"})
