#!/usr/bin/env python3
"""让宿主加载本仓库的当前代码：重生成 Codex 面 -> 换 cachebuster -> 重装。

Codex 把插件按版本号缓存到
``<codex-home>/plugins/cache/<marketplace>/<plugin>/<version>/``：**版本不变就不会
重新拷贝**。所以改完插件代码必须换一次 ``+codex.<token>`` 后缀，宿主才会用新副本。

    py -3 tools/reinstall.py            # 只体检 + 打印将要执行的动作（默认不动手）
    py -3 tools/reinstall.py --apply    # 真换版本并重装

依赖：``codex`` CLI 在 PATH 上；个人 marketplace 条目指向本仓库（见 --check 的输出）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows 上 stdout 被重定向时是 cp1252/cp936，中文进度行会直接 UnicodeEncodeError。
# 交互式控制台走 PEP 528 的宽字符 API，本来就支持中文，这里的 reconfigure 是空操作。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".codex-plugin" / "plugin.json"
MARKETPLACE = Path.home() / ".agents" / "plugins" / "marketplace.json"
CACHE_ROOT = Path.home() / ".codex" / "plugins" / "cache"
CACHEBUSTER_RE = re.compile(r"\+(?:codex)\.[a-z0-9-]+$", re.IGNORECASE)
# 宿主会把 commands/ 迁移成一份 source-command-<name> 技能放在这里。本插件已自带同功能的
# ddp-* 技能（skills/ 由同一批 commands/ 生成），两份同时在场只会让技能选择变模糊
# （实测：一个会话里 ddp-advance 与 source-command-advance 同时出现在可用技能列表里）。
MIGRATED_SKILLS_DIR = ".codex-plugin/migrated-command-skills"
# 宿主安装是**整棵工作树**拷贝（不是 git 的发布面），所以 .gitignore 排除的本机产物
# 会一起进缓存：实测 ddp 缓存 39.2 MB（.tmp 27 MB、.awr 2 MB、logs 0.9 MB、
# metastore_db + derby.log），而插件的发布面本身不到 2 MB。这些目录插件运行时一个都不用——
# 真正要用的是 `<用户项目>/.data-dev/`，由 /setup 在用户工程里现建。
CACHE_LOCAL_ARTIFACTS = (
    ".tmp",
    ".awr",
    "logs",
    "metastore_db",
    ".pytest_cache",
    "derby.log",
    "spark-warehouse",
    "warehouse",
)


def drop_migrated_skills(version: str, plugin_name: str) -> list:
    """重装后清掉宿主生成的迁移副本，避免同一功能两份技能同时被加载。"""
    removed: list = []
    for cache_dir in CACHE_ROOT.glob(f"*/{plugin_name}/{version}"):
        stale = cache_dir / MIGRATED_SKILLS_DIR
        if not stale.is_dir():
            continue
        _rmtree(stale)
        removed.append(str(stale))
    return removed


def _force_remove(function, path, _exc) -> None:
    """Windows 上只读文件会让 rmtree 报 WinError 5：先加写权限再删。

    实测来源：`.tmp/clonecheck-*` 是嵌套的 git 克隆，`.git/objects/**` 全是只读，
    第一次跑 prune 就死在 PermissionError 上（对象文件删不掉，整次重装中断）。
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        pass


def _rmtree(target: Path) -> None:
    kwargs = {"onexc": _force_remove} if sys.version_info >= (3, 12) else {"onerror": _force_remove}
    shutil.rmtree(target, **kwargs)


def prune_cache_artifacts(version: str, plugin_name: str) -> tuple:
    """把本机产物从**刚装进缓存的那份副本**里删掉，只保留插件的发布面。

    只动缓存副本，不动源码仓库：仓库里的 .tmp/ 是台账引用过的实验留档，删它等于毁证。
    """
    removed: list = []
    freed = 0
    for cache_dir in CACHE_ROOT.glob(f"*/{plugin_name}/{version}"):
        for name in CACHE_LOCAL_ARTIFACTS:
            target = cache_dir / name
            if target.is_dir():
                freed += sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
                _rmtree(target)
                removed.append(str(target))
            elif target.is_file():
                freed += target.stat().st_size
                target.unlink()
                removed.append(str(target))
    return removed, freed


def new_version(current: str, token: str) -> str:
    """保留 `+` 之前的基础版本，替换掉旧的 codex cachebuster。"""
    base = str(current).split("+", 1)[0]
    return f"{base}+codex.{token}"


def resolve_source(entry_path: str) -> Path:
    """Codex 把 marketplace 的 source.path 解析成相对**用户主目录**的路径。

    注意：不是相对 marketplace.json 所在目录——按后者解析会得到一个不存在的路径，
    这是本项目踩过的坑（`.agents/plugins/plugins/...` 并不存在）。
    """
    cleaned = entry_path.strip()
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return Path.home() / cleaned


def main() -> int:
    parser = argparse.ArgumentParser(description="reinstall this plugin for Codex")
    parser.add_argument("--apply", action="store_true", help="真的改版本并重装")
    parser.add_argument("--token", default=None, help="自定义 cachebuster（默认 UTC 时间戳）")
    args = parser.parse_args()

    if not MANIFEST.is_file():
        print(f"找不到清单：{MANIFEST}", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    plugin_name = str(manifest.get("name") or "")
    current = str(manifest.get("version") or "")
    if not plugin_name or not current:
        print("清单缺 name/version", file=sys.stderr)
        return 2

    # 1) 生成物与手写源保持一致（否则装了也是旧面）
    surface = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_codex_surface.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if surface.returncode != 0:
        print(surface.stdout + surface.stderr, file=sys.stderr)
        return surface.returncode
    print(f"Codex 面已按 commands/ 重生成（{len(surface.stdout.splitlines()) - 1} 个文件）")

    # 2) 前置条件：marketplace 条目必须指向本仓库
    marketplace_name = "personal"
    if not MARKETPLACE.is_file():
        print(f"没找到个人 marketplace：{MARKETPLACE}", file=sys.stderr)
        print("先按 plugin-creator 的脚手架流程建一个，再回来重装。", file=sys.stderr)
        return 2
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    marketplace_name = str(data.get("name") or marketplace_name)
    entry = next(
        (p for p in (data.get("plugins") or []) if isinstance(p, dict) and p.get("name") == plugin_name),
        None,
    )
    if entry is None:
        print(f"marketplace 里没有 {plugin_name} 条目，先建条目再重装。", file=sys.stderr)
        return 2
    source = resolve_source(str((entry.get("source") or {}).get("path") or ""))
    same = source.is_dir() and source.resolve() == ROOT.resolve()
    if not source.is_dir():
        print(f"marketplace 条目指向的源不存在：{source}", file=sys.stderr)
        print("把它指向本仓库（Windows 可用目录联接）：", file=sys.stderr)
        print(f'  New-Item -ItemType Junction -Path "{source}" -Target "{ROOT}"', file=sys.stderr)
        return 2
    print(f"marketplace 源：{source}  {'(就是本仓库)' if same else '(注意：不是本仓库，重装不会带上本次改动)'}")
    if not same:
        return 2

    # 3) 换 cachebuster
    token = args.token or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    token = re.sub(r"[^a-z0-9-]+", "-", token.strip().lower()).strip("-")
    target = new_version(current, token)
    print(f"版本：{current}  ->  {target}")

    if not args.apply:
        print()
        print("（当前是预演。加 --apply 才会真的改清单并重装）")
        return 0

    manifest["version"] = target
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写清单：{MANIFEST.relative_to(ROOT).as_posix()}")

    # 4) 重装
    codex = shutil.which("codex")
    if not codex:
        print("PATH 上没有 codex CLI；清单已更新，请手动执行：", file=sys.stderr)
        print(f"  codex plugin add {plugin_name}@{marketplace_name}", file=sys.stderr)
        return 2
    proc = subprocess.run(
        [codex, "plugin", "add", f"{plugin_name}@{marketplace_name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print((proc.stdout or "").strip())
    if proc.returncode != 0:
        print((proc.stderr or "").strip(), file=sys.stderr)
        return proc.returncode

    removed = drop_migrated_skills(target, plugin_name)
    if removed:
        print("已清掉宿主生成的迁移技能副本（与自带 ddp-* 技能重复）：")
        for item in removed:
            print(f"  - {item}")

    pruned, freed = prune_cache_artifacts(target, plugin_name)
    if pruned:
        print(f"已从缓存副本清掉本机产物（发布面之外，释放 {freed / 1024 / 1024:.1f} MB）：")
        for item in pruned:
            print(f"  - {item}")

    print()
    print("装好了。**新开一个线程**再用，宿主只在新会话里加载新版本。")
    print("重装不影响 hook 信任；只有改动 hooks/*.json 的内容才需要重新授权。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
