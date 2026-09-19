"""Path / IO helpers for the data-development-plugin runtime.

Layout inside a user's project (cwd) after ``/setup``:

    .data-dev/
      config.json                 # runtime mode + limits + CLI names
      current.json                # active ticket id (set by commands)
      tickets/<TICKET>/           # one req_dir per Tracker work item
        _state.json               # machine-readable pipeline state
        _context.md               # human/machine-readable state mirror
        requirement_spec.md       # P2-P6 contract bridge (built at end of P1)
        ... phase deliverables ...
      mock/                       # offline simulation state (mock mode only)
        tracker/workitems.json
        tracker/<TICKET>.comments.json
        baseline/<TICKET>/        # PUBLISHED baseline used by code_diff
      .runtime/                   # hook ledgers (retries / reads / decisions)
      runtime/                    # engine execution ledgers (executions / tasks / backfills)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR_NAME = ".data-dev"
CONFIG_FILE = "config.json"
TICKETS_DIR = "tickets"
MOCK_DIR = "mock"
RUNTIME_DIR = ".runtime"
LEDGER_DIR = "runtime"
CURRENT_FILE = "current.json"
STATE_FILE = "_state.json"
CONTEXT_MD = "_context.md"
SCHEMA_VERSION = 1
DEFAULT_KB_PREFIX = "viking://resources/"

PHASE_NAMES: Dict[int, str] = {
    0: "submitted(已提交/待SDS)",
    1: "phase1-sds(数据概要设计)",
    2: "phase2-architecture(架构设计)",
    3: "phase3-data-develop(数据研发)",
    4: "phase4-data-testing(数据测试)",
    5: "phase5-deploy(运维部署)",
    6: "phase6-semantic(语义层更新)",
    7: "done(已完成)",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def localnow() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(value: str) -> str:
    """Normalize a ticket id / table name to a filesystem-safe token."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    return cleaned.strip("-.") or "untitled"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError:
        return default


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def write_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def default_config() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "mock",  # mock | live
        "cli": {
            "tracker": "tracker",
            "platform": "platform",
            "quality": "quality",
            "knowledge": "openviking",
        },
        "live": {
            "allow_text": False,  # live CLI 必须输出 JSON；允许文本时显式打开
        },
        "knowledge": {
            "uri_prefix": DEFAULT_KB_PREFIX,
            # auto | http | ov-cli | custom-cli
            # auto：配置了 ov.url 走 HTTP 直连，否则走真实 ov CLI
            "kind": "auto",
            "limit": 10,
            "level": 2,
            "ov": {
                "url": "",  # OpenViking server，如 http://127.0.0.1:1933
                "api_key_env": "OV_API_KEY",  # 密钥环境变量（推荐）
                "api_key_file": "",  # 或本地密钥文件路径（首行即密钥，不入 git）
                "account": "",  # 可选 X-OpenViking-Account
                "user": "",  # 可选 X-OpenViking-User
                "timeout": 60,
            },
        },
        "platform": {
            # cli | mysql-family(mysql/doris/starrocks/tidb/oceanbase/generic-sql)
            # | command(paimon/flink/spark/trino/presto) | mcp(预留，会话内通道)
            "kind": "cli",
            "host": "",
            "port": 9030,
            "user": "",
            "password": "",
            "password_env": "DDP_PLATFORM_PASSWORD",
            "database": "",
            "connect_timeout": 10,
            # base_env 标识 platform 顶层 host/database 属于哪个环境；
            # profiles.<env> 为该环境的连接覆盖（没有 profiles.<env> 时只允许跑 base_env）
            "base_env": "dev",
            "profiles": {},
            "command": {
                "engine": "",
                "run": [],
                "check": [],
                "timeout": 600,
            },
            # 预留 MCP 通道契约：模型在会话内调用 mcp__<server>__<tool>，
            # 引擎 CLI 不直调。接入具体 MCP server 时填写 server 与 tools 映射。
            "mcp": {
                "server": "",
                "tools": {},
                "evidence_required": True,
            },
        },
        # 数仓分层/命名纪律开关：纯 MySQL/Doris/Paimon 等无分层湖仓可关掉
        "modeling": {
            "layer_policy": "enabled",  # enabled | disabled
            "prefixes": ["ods", "dwd", "dws", "ads", "dim", "mid"],
        },
        # P6 语义层开关：没有语义层/元数据目标的项目可关（P6 降级为交付登记）
        "semantic": {
            "enabled": True,
        },
        "deploy": {
            "require_rollback": False,
        },
        "limits": {
            "sql_count_min": 2,
            "pk_match_rate": 0.995,
            "dim_match_rate": 0.95,
            "key_null_rate": 0.0,
            "kb_round_max": 2,
            "test_count_min": {"L1": 1, "L2": 15, "L3": 25},
            "sql_grammar_retry_max": 3,
            "dataprocess_retry_max": 3,
            "read_cache_ttl_seconds": 7200,
            "evidence_max_age_hours": 24,
            "backfill_ledger_max_keys": 20,
        },
        "created_at": "",
        "updated_at": "",
    }


def project_root(start: Optional[Path] = None) -> Optional[Path]:
    """Find the nearest project root containing .data-dev/config.json."""
    cursor = (start or Path.cwd()).resolve()
    if cursor.is_file():
        cursor = cursor.parent
    for candidate in [cursor, *cursor.parents]:
        if (candidate / APP_DIR_NAME / CONFIG_FILE).is_file():
            return candidate
    return None


def require_project_root(start: Optional[Path] = None) -> Path:
    root = project_root(start)
    if root is None:
        raise FileNotFoundError(
            "未找到 .data-dev/config.json：请先运行 /setup 初始化 data-development-plugin 项目。"
        )
    return root


def config_path(root: Path) -> Path:
    return root / APP_DIR_NAME / CONFIG_FILE


def load_config(root: Path) -> Dict[str, Any]:
    cfg = load_json(config_path(root), {}) or {}
    merged = default_config()
    merged.update(cfg)

    def merge_nested(section: str) -> Dict[str, Any]:
        base = default_config()[section]
        out = dict(base)
        stored = cfg.get(section) or {}
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                sub = dict(base[key])
                sub.update(value)
                out[key] = sub
            else:
                out[key] = value
        return out

    cli = merge_nested("cli")
    merged["cli"] = cli
    merged["live"] = merge_nested("live")
    merged["knowledge"] = merge_nested("knowledge")
    merged["platform"] = merge_nested("platform")
    return merged


def ensure_app_dir(start: Optional[Path] = None, mode: str = "mock") -> Path:
    """Create .data-dev scaffolding. Returns project root."""
    root = (start or Path.cwd()).resolve()
    app = ensure_dir(root / APP_DIR_NAME)
    cfg = default_config()
    cfg["mode"] = mode if mode in ("mock", "live") else "mock"
    cfg["created_at"] = utcnow()
    cfg["updated_at"] = utcnow()
    write_json(config_path(root), cfg)
    ensure_dir(app / TICKETS_DIR)
    ensure_dir(app / MOCK_DIR / "tracker")
    ensure_dir(app / MOCK_DIR / "platform")
    ensure_dir(app / MOCK_DIR / "baseline")
    ensure_dir(app / RUNTIME_DIR)
    ensure_dir(app / LEDGER_DIR)
    return root


def set_current_ticket(root: Path, ticket_id: str) -> None:
    write_json(root / APP_DIR_NAME / CURRENT_FILE, {"ticket_id": ticket_id})


def current_ticket(root: Path) -> Optional[str]:
    data = load_json(root / APP_DIR_NAME / CURRENT_FILE, {}) or {}
    value = data.get("ticket_id")
    return str(value) if value else None


def ticket_dir(root: Path, ticket_id: str) -> Path:
    return root / APP_DIR_NAME / TICKETS_DIR / slugify(ticket_id)


def list_ticket_ids(root: Path) -> List[str]:
    base = root / APP_DIR_NAME / TICKETS_DIR
    if not base.is_dir():
        return []
    return sorted(
        entry.name
        for entry in base.iterdir()
        if entry.is_dir() and (entry / STATE_FILE).is_file()
    )


def resolve_ticket(root: Path, ticket_arg: Optional[str] = None) -> Optional[str]:
    """Order: explicit arg -> --ticket/--id inside arg -> current.json -> sole ticket."""
    if ticket_arg:
        return slugify(ticket_arg)
    current = current_ticket(root)
    if current:
        return current
    ids = list_ticket_ids(root)
    if len(ids) == 1:
        return ids[0]
    return None


def req_dir(root: Path, ticket_id: Optional[str] = None) -> Path:
    resolved = resolve_ticket(root, ticket_id)
    if resolved is None:
        raise FileNotFoundError("无法确定工单：请用 --ticket <id> 指定，或先运行 /start /sds-design 设置当前工单。")
    return ticket_dir(root, resolved)


def state_path(directory: Path) -> Path:
    return directory / STATE_FILE


def context_md_path(directory: Path) -> Path:
    return directory / CONTEXT_MD


def runtime_dir(root: Path) -> Path:
    return ensure_dir(root / APP_DIR_NAME / RUNTIME_DIR)


def ledger_dir(root: Path) -> Path:
    """引擎执行账本目录（``.data-dev/runtime``）。

    与 hook 账本 ``.data-dev/.runtime`` 分开：前者由 ``engine platform run-sql`` 等
    写入（executions / platform_tasks / backfill_runs），后者是 hook 的熔断/缓存账本。
    """
    return ensure_dir(root / APP_DIR_NAME / LEDGER_DIR)


def cli_output_dir(root: Path) -> Path:
    """Large CLI outputs are spilled to disk (mirrors cli-core.sh behavior)."""
    return ensure_dir(root / APP_DIR_NAME / ".cli-output")


def touch_runtime_ledger(root: Path, name: str) -> Dict[str, Any]:
    path = runtime_dir(root) / (name + ".json")
    data = load_json(path, {}) or {}
    return {"path": path, "data": data}


def save_runtime_ledger(path: Path, data: Dict[str, Any]) -> None:
    write_json(path, data)


def copy_tree(src: Path, dst: Path, ignore: Optional[set] = None) -> None:
    ignore = ignore or {"__pycache__"}
    if dst.exists():
        shutil.rmtree(dst)
    ensure_dir(dst.parent)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore))
