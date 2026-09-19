"""Adapters for the external systems the pipeline orchestrates.

Two transports share one interface:

* ``mock``  -- file-backed simulation under ``.data-dev/mock/`` so the whole
  pipeline can be exercised offline and tested deterministically;
* ``live``  -- delegates to real CLIs (``tracker``, ``platform``, ``quality``,
  ``openviking``) and expects JSON on stdout. Large outputs are spilled to
  ``.data-dev/.cli-output/`` (mirrors ``cli-core.sh`` behavior).

Nothing in ``core/`` hard-codes a transport: engine and hooks ask
``make_*`` for a client based on ``config.mode``.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import context, ov_knowledge

ALLOWED_KB_PREFIX = "viking://resources/"
STAGE_ORDER = ["已提交", "开发中", "待验收", "待测试", "待业务验收", "已交付", "待发布"]
SQL_PLATFORM_KINDS = ("mysql", "doris", "starrocks", "tidb", "oceanbase", "generic-sql")
COMMAND_PLATFORM_KINDS = ("paimon", "flink", "spark", "trino", "presto", "command")


def kb_prefix(config: Optional[Dict[str, Any]] = None) -> str:
    """Allowed knowledge source prefix from config (defaults to viking://resources/)."""
    knowledge = (config or {}).get("knowledge") or {}
    prefix = str(knowledge.get("uri_prefix") or "").strip()
    return prefix or ALLOWED_KB_PREFIX


class ClientError(RuntimeError):
    pass


def _run_cli(config: Dict[str, Any], root: Path, name: str, *args: str) -> Any:
    """Run a configured CLI, return parsed JSON or raise ClientError.

    ``cli.<name>`` 可以是可执行文件字符串，也可以是 argv 列表
    （如 ``["py", "-3", "tools/tracker.py"]``）。列表形式在 Windows 上是必需的：
    脚本类 CLI 若经 ``.cmd`` 包装器转发，多行内容参数会被 cmd.exe 的换行解析截断，
    后面的参数（例如 ``--anchor``）会整段丢失。
    """
    raw = (config.get("cli") or {}).get(name, name)
    argv = [str(part) for part in raw] if isinstance(raw, (list, tuple)) else [str(raw)]
    cli = argv[0]
    try:
        proc = subprocess.run(
            [*argv, *args],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except FileNotFoundError:
        raise ClientError(f"找不到 CLI: {cli}（请先运行 /setup 或配置 .data-dev/config.json cli.{name}）")
    except subprocess.TimeoutExpired:
        raise ClientError(f"CLI {cli} 超时")
    out = (proc.stdout or "").strip()
    spill = context.cli_output_dir(root) / f"{name}-{context.utcnow().replace(':', '-')}.out"
    if out:
        context.write_text(spill, out)
    if proc.returncode != 0:
        raise ClientError(f"CLI {cli} 退出码 {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:500]}")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        allow_text = bool((config.get("live") or {}).get("allow_text", False))
        if not allow_text:
            raise ClientError(
                f"CLI {cli} 输出不是合法 JSON（stdout 前 500 字见 {spill}）: {out[:500]}"
                "；如确为文本型 CLI，请配置 live.allow_text=true 或改用 kind=command。"
            )
        return {"output_file": str(spill), "stdout": out[:4000]}


# ---------------------------------------------------------------------------
# Tracker work items
# ---------------------------------------------------------------------------
class TrackerClient:
    def create_work_item(self, title: str, description_md: str, template_type: str = "数据任务") -> Dict[str, Any]:
        raise NotImplementedError

    def query(self, work_item_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def list_work_items(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def add_comment(self, work_item_id: str, content_md: str, anchor: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def comments(self, work_item_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def set_status(self, work_item_id: str, target: str, reason: str = "") -> Dict[str, Any]:
        raise NotImplementedError


class MockTrackerClient(TrackerClient):
    def __init__(self, root: Path):
        self.root = root
        self.store = root / context.APP_DIR_NAME / "mock" / "tracker"
        self.items_path = self.store / "workitems.json"
        context.ensure_dir(self.store)

    def _items(self) -> Dict[str, Any]:
        data = context.load_json(self.items_path, {"items": [], "seq": 0})
        if not isinstance(data, dict):
            data = {"items": [], "seq": 0}
        data.setdefault("items", [])
        data.setdefault("seq", len(data["items"]))
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        context.write_json(self.items_path, data)

    def _find(self, work_item_id: str) -> Dict[str, Any]:
        for item in self._items()["items"]:
            if item["id"] == work_item_id:
                return item
        raise ClientError(f"工单不存在: {work_item_id}")

    def create_work_item(self, title: str, description_md: str, template_type: str = "数据任务") -> Dict[str, Any]:
        data = self._items()
        seq = int(data.get("seq", 0)) + 1
        ticket_id = f"T-{seq:04d}"
        item = {
            "id": ticket_id,
            "title": title,
            "description_md": description_md,
            "templateTypeName": template_type,
            "status": STAGE_ORDER[0],
            "stage_index": 0,
            "created_at": context.utcnow(),
            "comments": [],
        }
        data["items"].insert(0, item)
        data["seq"] = seq
        self._save(data)
        return item

    def query(self, work_item_id: str) -> Dict[str, Any]:
        return dict(self._find(work_item_id))

    def list_work_items(self) -> List[Dict[str, Any]]:
        return [dict(i) for i in self._items()["items"]]

    def add_comment(self, work_item_id: str, content_md: str, anchor: str = "") -> Dict[str, Any]:
        data = self._items()
        item = next((i for i in data["items"] if i["id"] == work_item_id), None)
        if item is None:
            raise ClientError(f"工单不存在: {work_item_id}")
        comment = {
            "id": f"c-{len(item['comments']) + 1:03d}",
            "anchor": anchor,
            "content_md": content_md,
            "created_at": context.utcnow(),
        }
        item["comments"].append(comment)
        self._save(data)
        return comment

    def comments(self, work_item_id: str) -> List[Dict[str, Any]]:
        return [dict(c) for c in self._find(work_item_id)["comments"]]

    def set_status(self, work_item_id: str, target: str, reason: str = "") -> Dict[str, Any]:
        data = self._items()
        item = next((i for i in data["items"] if i["id"] == work_item_id), None)
        if item is None:
            raise ClientError(f"工单不存在: {work_item_id}")
        if target not in STAGE_ORDER:
            raise ClientError(f"非法状态: {target}（合法: {STAGE_ORDER}）")
        current = item.get("stage_index", 0)
        target_index = STAGE_ORDER.index(target)
        if target_index > current + 1:
            raise ClientError(f"不允许跨 stage 跳跃: {item['status']} -> {target}")
        item["status"] = target
        item["stage_index"] = target_index
        if reason:
            item.setdefault("status_log", []).append({"to": target, "reason": reason, "at": context.utcnow()})
        self._save(data)
        return {"id": work_item_id, "status": target}


class LiveTrackerClient(TrackerClient):
    def __init__(self, config: Dict[str, Any], root: Path):
        self.config = config
        self.root = root

    def create_work_item(self, title: str, description_md: str, template_type: str = "数据任务") -> Dict[str, Any]:
        return _run_cli(
            self.config, self.root, "tracker", "create",
            "--title", title, "--description", description_md, "--templateTypeName", template_type,
        )

    def query(self, work_item_id: str) -> Dict[str, Any]:
        return _run_cli(self.config, self.root, "tracker", "show", "--id", work_item_id)

    def list_work_items(self) -> List[Dict[str, Any]]:
        result = _run_cli(self.config, self.root, "tracker", "list")
        return result if isinstance(result, list) else result.get("items", [])

    def add_comment(self, work_item_id: str, content_md: str, anchor: str = "") -> Dict[str, Any]:
        args = ["comment", "--id", work_item_id, "--content", content_md]
        if anchor:
            args += ["--anchor", anchor]
        return _run_cli(self.config, self.root, "tracker", *args)

    def comments(self, work_item_id: str) -> List[Dict[str, Any]]:
        return _run_cli(self.config, self.root, "tracker", "comments", "--id", work_item_id)

    def set_status(self, work_item_id: str, target: str, reason: str = "") -> Dict[str, Any]:
        args = ["set-status", "--id", work_item_id, "--status", target]
        if reason:
            args += ["--reason", reason]
        return _run_cli(self.config, self.root, "tracker", *args)


def make_tracker(config: Dict[str, Any], root: Path) -> TrackerClient:
    if (config.get("mode") or "mock") == "live":
        return LiveTrackerClient(config, root)
    return MockTrackerClient(root)


# ---------------------------------------------------------------------------
# platform (dev + prod execution)
# ---------------------------------------------------------------------------
class PlatformClient:
    def run_sql(self, sql: str, project: str = "dev", env: str = "dev") -> Dict[str, Any]:
        raise NotImplementedError

    def check_grammar(self, sql: str) -> Dict[str, Any]:
        raise NotImplementedError

    def create_task(self, task_name: str, code: str, schedule: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def read_task(self, task_name: str) -> Dict[str, Any]:
        raise NotImplementedError

    def update_task(self, task_name: str, code: str) -> Dict[str, Any]:
        raise NotImplementedError

    def submit_task(self, task_name: str) -> Dict[str, Any]:
        raise NotImplementedError

    def publish_task(self, task_name: str, comment: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def backfill(self, task_name: str, start: str, end: str) -> Dict[str, Any]:
        raise NotImplementedError


class MockPlatformClient(PlatformClient):
    def __init__(self, root: Path):
        self.root = root
        self.dir = root / context.APP_DIR_NAME / "mock" / "platform"
        self.tasks_path = self.dir / "tasks.json"
        self.runs_path = self.dir / "runs.json"
        context.ensure_dir(self.dir)
        for path in (self.tasks_path, self.runs_path):
            if not path.exists():
                context.write_json(path, {"items": [], "seq": 0})

    def _load(self, path: Path) -> Dict[str, Any]:
        return context.load_json(path, {"items": [], "seq": 0})

    def _task(self, task_name: str) -> Dict[str, Any]:
        for item in self._load(self.tasks_path)["items"]:
            if item["task_name"] == task_name:
                return item
        raise ClientError(f"platform 任务不存在: {task_name}")

    def run_sql(self, sql: str, project: str = "dev", env: str = "dev") -> Dict[str, Any]:
        record = {
            "query_id": "q_" + context.sha256_text(sql)[:14],
            "status": "FAILED" if "FAIL_ME" in sql else "SUCCESS",
            "project": project,
            "env": env,
            "at": context.utcnow(),
        }
        runs = self._load(self.runs_path)
        runs.setdefault("items", []).append(record)
        context.write_json(self.runs_path, runs)
        return record

    def check_grammar(self, sql: str) -> Dict[str, Any]:
        ok = "(" in sql and ")" in sql and "FAIL_ME" not in sql
        return {"valid": ok, "errors": [] if ok else ["mock: FAIL_ME 触发生成语法错误"]}

    def create_task(self, task_name: str, code: str, schedule: str = "") -> Dict[str, Any]:
        data = self._load(self.tasks_path)
        for item in data["items"]:
            if item["task_name"] == task_name:
                raise ClientError(f"任务已存在: {task_name}（请用 update）")
        task = {
            "task_name": task_name,
            "code": code,
            "schedule": schedule,
            "status": "DRAFT",
            "created_at": context.utcnow(),
            "content_hash": context.sha256_text(code),
        }
        data["items"].append(task)
        context.write_json(self.tasks_path, data)
        return dict(task)

    def read_task(self, task_name: str) -> Dict[str, Any]:
        return dict(self._task(task_name))

    def update_task(self, task_name: str, code: str) -> Dict[str, Any]:
        task = self._task(task_name)
        task["code"] = code
        task["content_hash"] = context.sha256_text(code)
        task["status"] = "DRAFT"
        data = self._load(self.tasks_path)
        for item in data["items"]:
            if item["task_name"] == task_name:
                item.update(task)
        context.write_json(self.tasks_path, data)
        return dict(task)

    def submit_task(self, task_name: str) -> Dict[str, Any]:
        task = self._task(task_name)
        task["status"] = "SUBMITTED"
        data = self._load(self.tasks_path)
        for item in data["items"]:
            if item["task_name"] == task_name:
                item.update(task)
        context.write_json(self.tasks_path, data)
        return {"task_name": task_name, "status": "SUBMITTED"}

    def publish_task(self, task_name: str, comment: str = "") -> Dict[str, Any]:
        task = self._task(task_name)
        task["status"] = "PUBLISHED"
        task["published_at"] = context.utcnow()
        task["publish_comment"] = comment
        data = self._load(self.tasks_path)
        for item in data["items"]:
            if item["task_name"] == task_name:
                item.update(task)
        context.write_json(self.tasks_path, data)
        return {"task_name": task_name, "status": "PUBLISHED"}

    def backfill(self, task_name: str, start: str, end: str) -> Dict[str, Any]:
        self._task(task_name)
        # mock 也要写回刷账本：P5 出口门按 key 反查，mock 不写就等于这道门在 demo 里是空的。
        ledger_path = context.ledger_dir(self.root) / "backfill_runs.json"
        ledger = context.load_json(ledger_path, {"runs": []}) or {"runs": []}
        key = f"{task_name}|{start}|{end}"
        ledger.setdefault("runs", []).append(
            {"key": key, "date": str(start), "status": "SUCCESS", "query_id": "", "at": context.utcnow()}
        )
        context.write_json(ledger_path, ledger)
        return {
            "task_name": task_name,
            "range": [start, end],
            "status": "SUCCESS",
            "run_id": "bf_" + context.sha256_text(f"{task_name}{start}{end}")[:12],
            "at": context.utcnow(),
        }


class LivePlatformClient(PlatformClient):
    def __init__(self, config: Dict[str, Any], root: Path):
        self.config = config
        self.root = root

    def _call(self, *args: str) -> Any:
        return _run_cli(self.config, self.root, "platform", *args)

    def run_sql(self, sql: str, project: str = "dev", env: str = "dev") -> Dict[str, Any]:
        return self._call("runSql", "--sql", sql, "--project", project, "--env", env)

    def check_grammar(self, sql: str) -> Dict[str, Any]:
        return self._call("checkSqlGrammar", "--sql", sql)

    def create_task(self, task_name: str, code: str, schedule: str = "") -> Dict[str, Any]:
        args = ["createTask", "--name", task_name, "--code", code]
        if schedule:
            args += ["--schedule", schedule]
        return self._call(*args)

    def read_task(self, task_name: str) -> Dict[str, Any]:
        return self._call("readTask", "--name", task_name)

    def update_task(self, task_name: str, code: str) -> Dict[str, Any]:
        return self._call("updateTask", "--name", task_name, "--code", code)

    def submit_task(self, task_name: str) -> Dict[str, Any]:
        return self._call("submit", "--name", task_name)

    def publish_task(self, task_name: str, comment: str = "") -> Dict[str, Any]:
        args = ["publish", "--name", task_name]
        if comment:
            args += ["--comment", comment]
        return self._call(*args)

    def backfill(self, task_name: str, start: str, end: str) -> Dict[str, Any]:
        return self._call("backfill", "--name", task_name, "--start", start, "--end", end)


def make_platform(config: Dict[str, Any], root: Path) -> PlatformClient:
    mode = config.get("mode") or "mock"
    kind = str((config.get("platform") or {}).get("kind") or "cli")
    if mode == "live":
        from . import sql_platforms  # local import avoids circular dependency

        if kind in SQL_PLATFORM_KINDS:
            return sql_platforms.SqlPlatformClient(config, root)
        if kind in COMMAND_PLATFORM_KINDS:
            return sql_platforms.CommandPlatformClient(config, root)
        if kind == "mcp":
            raise ClientError(
                "kind=mcp 是预留的会话内通道：请由 Claude Code 模型调用 "
                "mcp__<server>__<tool>，引擎 CLI 不支持直调。接入契约见 "
                "docs/REPRODUCTION-NOTES.md 1.2.2。"
            )
    if mode == "live":
        return LivePlatformClient(config, root)
    return MockPlatformClient(root)


# ---------------------------------------------------------------------------
# openviking knowledge base (SDS 溯源）
# ---------------------------------------------------------------------------
class KnowledgeClient:
    def search(self, query: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def read(self, uri: str) -> str:
        raise NotImplementedError


class MockKnowledgeClient(KnowledgeClient):
    def __init__(self, root: Path, prefix: str = ALLOWED_KB_PREFIX):
        self.kb_dir = root / context.APP_DIR_NAME / "mock" / "kb"
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        context.ensure_dir(self.kb_dir)
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        index = self.kb_dir / "index.json"
        if index.exists():
            return
        context.write_json(
            index,
            {
                "documents": [
                    {
                        "uri": f"{self.prefix}domain/dwd-user/readme.md",
                        "domain": "dwd-user",
                        "layer": "dwd",
                        "tables": ["dwd_user_di"],
                        "abstract": "用户域明细层：user_id 主键口径、注册/活跃时间、null_rate=0。",
                    },
                    {
                        "uri": f"{self.prefix}domain/dim-date/readme.md",
                        "domain": "dim-date",
                        "layer": "dim",
                        "tables": ["dim_date"],
                        "abstract": "日期维表：ds 分区连续、join_match_rate 基准 0.999。",
                    },
                ]
            },
        )

    def search(self, query: str) -> List[Dict[str, Any]]:
        docs = (context.load_json(self.kb_dir / "index.json") or {}).get("documents", [])
        query_l = query.lower()
        hits = []
        for doc in docs:
            haystack = " ".join(
                [doc.get("uri", ""), doc.get("domain", ""), doc.get("abstract", ""), " ".join(doc.get("tables", []))]
            ).lower()
            score = 1 if any(t in haystack for t in query_l.split()) else 0
            if score:
                hits.append(dict(doc))
        return hits

    def read(self, uri: str) -> str:
        if not uri.startswith(self.prefix):
            raise ClientError(f"知识源越界: {uri}（铁律: 必须以 {self.prefix} 开头）")
        for doc in (context.load_json(self.kb_dir / "index.json") or {}).get("documents", []):
            if doc.get("uri") == uri:
                return doc.get("abstract", "")
        raise ClientError(f"知识文档不存在: {uri}")


class LiveKnowledgeClient(KnowledgeClient):
    def __init__(self, config: Dict[str, Any], root: Path):
        self.config = config
        self.root = root
        self.prefix = kb_prefix(config)
        knowledge = config.get("knowledge") or {}
        self.kind = str(knowledge.get("kind") or "auto").lower()
        self.ov_cfg = knowledge.get("ov") or {}
        self.has_http_url = bool(str(self.ov_cfg.get("url") or "").strip())

    def _transport(self) -> str:
        if self.kind == "http" or (self.kind == "auto" and self.has_http_url):
            if not self.has_http_url:
                raise ClientError("knowledge.kind=http 但未配置 knowledge.ov.url")
            return "http"
        if self.kind in ("custom-cli", "custom"):
            return "custom-cli"
        if self.kind in ("ov-cli", "auto", ""):
            return "ov-cli"
        raise ClientError(f"未知 knowledge.kind: {self.kind}（auto|http|ov-cli|custom-cli）")

    def _level(self) -> int:
        try:
            return int((self.config.get("knowledge") or {}).get("level") or 2)
        except (TypeError, ValueError):
            return 2

    def search(self, query: str) -> List[Dict[str, Any]]:
        transport = self._transport()
        if transport == "http":
            try:
                return ov_knowledge.find(self.config, query)
            except ov_knowledge.OpenVikingError as exc:
                raise ClientError(f"OpenViking find 失败[{exc.code}]: {exc}") from exc
        if transport == "custom-cli":
            result = _run_cli(self.config, self.root, "knowledge", "search", "--query", query)
            return result if isinstance(result, list) else result.get("results", [])
        # ov-cli：ov find "<query>" -u <prefix> -L <level> -o json
        result = _run_cli(
            self.config,
            self.root,
            "knowledge",
            "find",
            str(query or ""),
            "--uri",
            self.prefix.rstrip("/"),
            "--level",
            str(self._level()),
            "-o",
            "json",
        )
        try:
            return ov_knowledge.normalize_find_result(result)
        except ov_knowledge.OpenVikingError as exc:
            raise ClientError(f"OpenViking find 输出解析失败[{exc.code}]: {exc}") from exc

    def read(self, uri: str) -> str:
        if not uri.startswith(self.prefix):
            raise ClientError(f"知识源越界: {uri}（铁律: 必须以 {self.prefix} 开头）")
        transport = self._transport()
        if transport == "http":
            try:
                return ov_knowledge.read(self.config, uri)
            except ov_knowledge.OpenVikingError as exc:
                raise ClientError(f"OpenViking read 失败[{exc.code}]: {exc}") from exc
        if transport == "custom-cli":
            result = _run_cli(self.config, self.root, "knowledge", "read", "--uri", uri)
            if isinstance(result, dict):
                return str(result.get("content", result))
            return str(result)
        # ov-cli：ov read <uri> -o json
        result = _run_cli(self.config, self.root, "knowledge", "read", uri, "-o", "json")
        try:
            return ov_knowledge.normalize_read_result(result)
        except ov_knowledge.OpenVikingError as exc:
            raise ClientError(f"OpenViking read 输出解析失败[{exc.code}]: {exc}") from exc


def make_knowledge(config: Dict[str, Any], root: Path) -> KnowledgeClient:
    if (config.get("mode") or "mock") == "live":
        return LiveKnowledgeClient(config, root)
    return MockKnowledgeClient(root, kb_prefix(config))


def parse_sql_write_targets(sql: str) -> List[str]:
    """Table names targeted by write statements (for backfill/read guards)."""
    targets = re.findall(r"(?:INSERT\s+(?:OVERWRITE|INTO)\s+TABLE|UPDATE|DELETE\s+FROM|ALTER\s+TABLE)\s+([`\"\[\]]?[\w$.-]+)", sql, re.IGNORECASE)
    return [t.strip().strip("`\"[]") for t in targets]
