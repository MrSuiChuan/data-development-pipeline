#!/usr/bin/env python3
"""Generate the Codex-facing surface of data-development-plugin from the Claude Code sources.

Repo layout after running this script (dual-host plugin):

    .claude-plugin/plugin.json      Claude Code 清单（手写）
    .codex-plugin/plugin.json       Codex 清单（本脚本生成）
    commands/*.md                   Claude Code 斜杠命令（手写，唯一事实源）
    skills/*/SKILL.md               两宿主共用的方法论技能（手写）
    skills/ddp-*/SKILL.md           Codex 入口技能（本脚本从 commands/ 生成）
    hooks/hooks.json                Claude Code hook 配置（tools/adapt_hooks.py 切换解释器）
    hooks/claude-codex-hooks.json   两宿主共用 hook 配置（本脚本生成）

Codex 没有斜杠命令，用户入口是技能，所以 `/submit` → 技能 `ddp-submit`。
生成物都带「请勿手改」抬头；改命令请改 commands/*.md 后重跑本脚本。

    py -3 tools/build_codex_surface.py        # Windows
    python3 tools/build_codex_surface.py      # macOS/Linux
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = ROOT / "commands"
SKILLS = ROOT / "skills"
HOOKS = ROOT / "hooks"
TEMPLATES = ROOT / "engine" / "templates"

PLUGIN_NAME = "data-development-plugin"
# 署名与 LICENSE 的 Copyright 保持一致；不用空 email 字段——SECURITY.md 之类的
# 文档会因此指向一个不存在的东西（这条踩过）。
PLUGIN_AUTHOR = "AI实战技能圈"
CLAUDE_MANIFEST = ROOT / ".claude-plugin" / "plugin.json"


def base_version() -> str:
    """基础版本的唯一来源：手写的 Claude 清单，其余位置全部由它派生。

    原先这里硬编码 ``VERSION = "1.2.2"``，与 Claude 清单、CHANGELOG 三处各写一份，
    谁都不会发现不一致（DEVEX-002）。`+codex.<token>` 后缀由 `tools/reinstall.py`
    维护（宿主按版本号缓存插件），生成器只保留后缀、不重新发明基础版本。
    """
    try:
        data = json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SystemExit(f"读不到基础版本：{CLAUDE_MANIFEST}（{err}）")
    version = str(data.get("version") or "").strip()
    if not version:
        raise SystemExit(f"{CLAUDE_MANIFEST} 缺 version")
    return version

COMMAND_NAMES = (
    "setup",
    "submit",
    "sds-design",
    "start",
    "copilot",
    "resume",
    "advance",
    "status",
    "overview",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print("wrote", path.relative_to(ROOT).as_posix())


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` frontmatter from the command body."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line and not line.startswith((" ", "-", "\t")):
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, body


def adapt_body(body: str) -> str:
    """Translate Claude-Code-specific vocabulary into Codex equivalents."""
    for name in COMMAND_NAMES:
        body = re.sub(rf"(?<![\w/-])/{re.escape(name)}\b", f"{PLUGIN_NAME}:ddp-{name}", body)
    body = body.replace("$ARGUMENTS", "用户输入").replace("${ARGUMENTS}", "用户输入")
    body = body.replace(
        "AskUserQuestion",
        "向用户提问（Codex 里直接在对话中问，等用户回复，不要自答）",
    )
    body = body.replace(
        "`Task`",
        "子代理（Codex 用 spawn_agent 起子代理，或按 `agents/<角色>.md` 的角色定义就地执行）",
    )
    return body


def build_command_skills() -> None:
    for name in COMMAND_NAMES:
        source = COMMANDS / f"{name}.md"
        if not source.is_file():
            print("skip (missing command):", source.name)
            continue
        meta, body = split_frontmatter(read(source))
        description = meta.get("description", f"{name} 命令（Codex 版入口技能）")
        header = (
            f"---\nname: ddp-{name}\ndescription: {description}（Codex 入口，等价于 /{name}）\n---\n\n"
            f"# ddp-{name}（Codex 版 `/{name}`）\n\n"
            f"> 本文件由 `tools/build_codex_surface.py` 从 `commands/{name}.md` 生成，请勿手改。\n"
            f"> 改流程请改命令源文件后重跑生成器。\n"
            f"> 命令里的 `py -3` 是 Windows 写法；macOS/Linux 换成 `python3`。\n\n"
        )
        write(SKILLS / f"ddp-{name}" / "SKILL.md", header + adapt_body(body).rstrip() + "\n")


def build_codex_hooks() -> None:
    """Shared hook config for Claude Code + Codex.

    Codex 的 hook 契约与 Claude Code 相同（stdin 收 JSON、stdout 出
    ``hookSpecificOutput``、``permissionDecision: deny`` 硬阻断），并且同样注入
    ``CLAUDE_PLUGIN_ROOT``，所以两个宿主共用同一份配置即可。
    """
    claude = json.loads(read(HOOKS / "hooks.json") or "{}")
    config = {
        "description": (
            "data-development-plugin 三层防线：PreToolUse 硬门（permissionDecision=deny / "
            "decision:block）+ PostToolUse 验证器软提示（写 context 键）+ SessionStart 状态提醒。"
            "Claude Code 与 Codex 共用本文件；Windows 用 py -3，macOS/Linux 跑 "
            "tools/adapt_hooks.py 切换为 python3。"
        ),
        "hooks": claude.get("hooks", {}),
    }
    write(HOOKS / "claude-codex-hooks.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")


def build_codex_manifest() -> None:
    manifest_path = ROOT / ".codex-plugin" / "plugin.json"
    # 保留 tools/reinstall.py 写的 `+codex.<token>` 后缀，避免重跑生成器把它抹掉
    base = base_version()
    version = base
    existing = read(manifest_path)
    if existing:
        try:
            current = str(json.loads(existing).get("version") or "")
        except json.JSONDecodeError:
            current = ""
        if "+" in current:
            version = f"{base}+{current.split('+', 1)[1]}"
    manifest = {
        "name": PLUGIN_NAME,
        "version": version,
        "description": (
            "通用离线数据任务研发流水线：六阶段状态机（SDS → 架构 → 研发 → 测试 → 部署 → 语义层）"
            "+ Hook 硬门控，强制按顺序交付机器证据。任务单/数据平台/质量/知识库均为可配置连接器。"
        ),
        "author": {"name": PLUGIN_AUTHOR},
        "license": "Apache-2.0",
        "keywords": [
            "data-development",
            "pipeline",
            "state-machine",
            "gates",
            "offline-tasks",
            "doris",
        ],
        "skills": "./skills/",
        "hooks": "./hooks/claude-codex-hooks.json",
        "interface": {
            "displayName": "Data Development Pipeline",
            "shortDescription": "六阶段数据研发流水线 + Hook 硬门控",
            "longDescription": (
                "把离线数据任务研发工艺翻译成状态机 + 硬门控：AI 在门内自主干活，"
                "阶段出口只认机器证据（执行账本 query_id、test_runs.json、[已确认发布] token）。"
                "Claude Code 用斜杠命令，Codex 用 ddp-* 技能，两者共用同一引擎与 hook。"
            ),
            "developerName": PLUGIN_AUTHOR,
            "category": "Productivity",
            "capabilities": ["Instructions", "Lifecycle hooks", "Write"],
            "defaultPrompt": [
                "初始化数开插件并建一个数据需求工单",
                "用数开流水线把这张表改造成加列 + 回刷",
                "检查当前工单的阶段出口门为什么没通过",
            ],
            "brandColor": "#2F6FEB",
        },
    }
    write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def build_agents_template() -> None:
    """Codex 读 AGENTS.md，Claude Code 读 CLAUDE.md —— 同一份规范两个文件名。"""
    claude_template = read(TEMPLATES / "CLAUDE.ddp.md")
    if not claude_template:
        print("skip (missing template): engine/templates/CLAUDE.ddp.md")
        return
    body = claude_template.replace(
        "# data-development-plugin 研发编码规范（由 /setup 注入）",
        "# data-development-plugin 研发编码规范（由 setup 技能注入；Codex 读 AGENTS.md）",
    )
    body = body.replace("AskUserQuestion", "向用户提问（Codex 里直接对话问）")
    write(TEMPLATES / "AGENTS.ddp.md", body)


def main() -> int:
    build_codex_manifest()
    build_codex_hooks()
    build_command_skills()
    build_agents_template()
    print("\nCodex surface generated. Next: python tools/adapt_hooks.py (POSIX) / codex plugin add")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
