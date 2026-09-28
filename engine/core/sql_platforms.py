"""Pluggable SQL data-source adapters for offline task development.

Two families are supported through ``platform.kind``:

* MySQL-protocol engines (``mysql`` / ``doris`` / ``starrocks`` / ``tidb`` /
  ``oceanbase`` / ``generic-sql``): executed over a direct DB-API connection
  (``pymysql``) using ``platform.host/port/user/database``. Password comes from
  ``platform.password`` or the env var named by ``platform.password_env``.
* Command-run engines (``paimon`` / ``flink`` / ``spark`` / ``trino`` /
  ``presto`` / ``command``): every SQL statement is written to a temp file and
  handed to a configured argv template, e.g. ``["flink", "sql", "-f", "{file}"]``.
  This covers engines that do not speak a simple JDBC protocol from this process
  (Flink/Spark/Paimon catalogs, Presto/Trino CLIs, or any project bridge script).

Open engines do not expose a server-side ``query_id`` over these transports, so
execution evidence uses an adapter-generated ``query_id``
(``run_<sha256(sql+timestamp)[:14]>``) plus ``affected_rows`` / ``executed_at`` /
``duration_s``. Treat it as an execution receipt, not a platform query id.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from . import clients, context, engines


def expand_bizdate(sql: str, date_value: str) -> str:
    """Replace ${bizdate} / {bizdate} / $bizdate placeholders with a date."""
    return re.sub(r"\$\{bizdate\}|\{bizdate\}|\$bizdate", date_value, sql)


class TaskLedgerMixin:
    """Local task-code snapshots shared by every file-based platform adapter."""

    def _load_tasks(self) -> Dict[str, Any]:
        return context.load_json(self._ledger, {"items": []}) or {"items": []}

    def _save_tasks(self, data: Dict[str, Any]) -> None:
        context.write_json(self._ledger, data)

    def _find(self, task_name: str) -> Dict[str, Any]:
        for item in self._load_tasks()["items"]:
            if item["task_name"] == task_name:
                return item
        raise clients.ClientError(f"{self.engine} 任务不存在: {task_name}（先 create-task）")

    def create_task(self, task_name: str, code: str, schedule: str = "") -> Dict[str, Any]:
        data = self._load_tasks()
        if any(t["task_name"] == task_name for t in data["items"]):
            raise clients.ClientError(f"任务已存在: {task_name}（请用 update）")
        task = {
            "task_name": task_name,
            "code": code,
            "schedule": schedule,
            "status": "DRAFT",
            "created_at": context.utcnow(),
            "content_hash": context.sha256_text(code),
        }
        data.setdefault("items", []).append(task)
        self._save_tasks(data)
        return dict(task)

    def read_task(self, task_name: str) -> Dict[str, Any]:
        return dict(self._find(task_name))

    def update_task(self, task_name: str, code: str) -> Dict[str, Any]:
        data = self._load_tasks()
        task = next((t for t in data["items"] if t["task_name"] == task_name), None)
        if task is None:
            raise clients.ClientError(f"{self.engine} 任务不存在: {task_name}（先 create-task）")
        task.update({"code": code, "content_hash": context.sha256_text(code), "status": "DRAFT"})
        self._save_tasks(data)
        return dict(task)

    def submit_task(self, task_name: str) -> Dict[str, Any]:
        data = self._load_tasks()
        task = next((t for t in data["items"] if t["task_name"] == task_name), None)
        if task is None:
            raise clients.ClientError(f"{self.engine} 任务不存在: {task_name}（先 create-task）")
        task["status"] = "SUBMITTED"
        self._save_tasks(data)
        return {"task_name": task_name, "status": "SUBMITTED"}

    def publish_task(self, task_name: str, comment: str = "") -> Dict[str, Any]:
        data = self._load_tasks()
        task = next((t for t in data["items"] if t["task_name"] == task_name), None)
        if task is None:
            raise clients.ClientError(f"{self.engine} 任务不存在: {task_name}（先 create-task）")
        task["status"] = "PUBLISHED"
        task["published_at"] = context.utcnow()
        task["publish_comment"] = comment
        self._save_tasks(data)
        return {"task_name": task_name, "status": "PUBLISHED"}

    def backfill(self, task_name: str, start: str, end: str) -> Dict[str, Any]:
        task = self._find(task_name)
        return self.backfill_code(task["code"], start, end, task_name=task_name, env="prod")

    def backfill_code(
        self,
        code: str,
        start: str,
        end: str,
        task_name: str = "",
        env: str = "prod",
    ) -> Dict[str, Any]:
        if not code or not str(code).strip():
            raise clients.ClientError(f"{task_name or 'task'} 无可执行代码")
        if "${bizdate}" not in code and "{bizdate}" not in code and "$bizdate" not in code:
            # 单次回刷（无 bizdate 占位符）也留账本，否则 P5 出口门按 key 反查时
            # 会把这条合法路径误判成"没回刷"。
            record = self.run_sql(code, env=env)
            ledger_path = context.ledger_dir(self.root) / "backfill_runs.json"
            ledger = context.load_json(ledger_path, {"runs": []}) or {"runs": []}
            key = f"{task_name}|{start}|{end}"
            ledger.setdefault("runs", []).append(
                {
                    "key": key,
                    "date": str(start),
                    "status": "SUCCESS",
                    "query_id": str(record.get("query_id") or ""),
                    "at": context.utcnow(),
                }
            )
            context.write_json(ledger_path, ledger)
            return {
                "task_name": task_name,
                "range": [str(start), str(end)],
                "status": "SUCCESS",
                "run_id": "bf_" + context.sha256_text(f"{task_name}{start}{end}")[:12],
                "runs": [record],
                "skipped_resumed": [],
                "at": context.utcnow(),
            }
        dates = _date_range(str(start), str(end))
        if not dates:
            raise clients.ClientError("backfill 日期范围非法或超过 90 天")
        ledger_path = context.ledger_dir(self.root) / "backfill_runs.json"
        ledger = context.load_json(ledger_path, {"runs": []}) or {"runs": []}
        key = f"{task_name}|{start}|{end}"
        done = {
            str(r.get("date"))
            for r in ledger.get("runs", [])
            if r.get("key") == key and r.get("status") == "SUCCESS"
        }
        runs: List[Dict[str, Any]] = []
        skipped: List[str] = []
        for day in dates:
            if day in done:
                skipped.append(day)
                continue
            try:
                record = self.run_sql(expand_bizdate(code, day), env=env)
                record = {"key": key, "date": day, "status": "SUCCESS", "query_id": record.get("query_id", ""), "at": context.utcnow()}
            except Exception as exc:
                ledger.setdefault("runs", []).append(
                    {"key": key, "date": day, "status": "FAILED", "error": str(exc)[:300], "at": context.utcnow()}
                )
                context.write_json(ledger_path, ledger)
                raise
            ledger.setdefault("runs", []).append(record)
            context.write_json(ledger_path, ledger)
            runs.append(record)
        self._prune_backfill_ledger(ledger_path, ledger)
        return {
            "task_name": task_name,
            "range": [str(start), str(end)],
            "status": "SUCCESS",
            "run_id": "bf_" + context.sha256_text(f"{task_name}{start}{end}")[:12],
            "runs": runs,
            "skipped_resumed": skipped,
            "at": context.utcnow(),
        }

    def _prune_backfill_ledger(self, ledger_path: Path, ledger: Dict[str, Any]) -> None:
        """Keep only the most recent N distinct ranges (default 20)."""
        max_keys = int((self.config.get("limits") or {}).get("backfill_ledger_max_keys", 20) or 20)
        runs = ledger.get("runs") or []
        if max_keys <= 0:
            return
        order: List[str] = []
        seen = set()
        for run in reversed(runs):
            key = str(run.get("key") or "")
            if key and key not in seen:
                seen.add(key)
                order.append(key)
        order.reverse()
        keep = set(order[-max_keys:])
        pruned = [run for run in runs if str(run.get("key") or "") in keep]
        if len(pruned) != len(runs):
            ledger["runs"] = pruned
            context.write_json(ledger_path, ledger)


class SqlPlatformClient(TaskLedgerMixin, clients.PlatformClient):
    """MySQL-protocol engines via pymysql (Doris / StarRocks / MySQL / TiDB ...)."""

    def __init__(self, config: Dict[str, Any], root: Path):
        self.config = config
        self.root = Path(root)
        self.engine = str((config.get("platform") or {}).get("kind") or "mysql")
        self._ledger = context.ledger_dir(self.root) / "platform_tasks.json"

    def _effective_config(self, env: Optional[str] = None) -> Dict[str, Any]:
        p = self.config.get("platform") or {}
        base_env = str(p.get("base_env") or "dev")
        target = str(env or base_env)
        profiles = p.get("profiles") or {}
        if target in profiles:
            merged = {k: p.get(k) for k in ("host", "port", "user", "password", "password_env", "database", "connect_timeout")}
            merged.update(profiles.get(target) or {})
            return merged
        if target == base_env:
            return p
        raise clients.ClientError(
            f"{self.engine} 未配置环境 {target!r}：请在 platform.profiles.{target} 提供连接参数"
            "（base_env=" + base_env + "），防止把非目标环境的 SQL 打到错误数据库。"
        )

    def _params(self, env: Optional[str] = None) -> Dict[str, Any]:
        p = self._effective_config(env)
        password = str(p.get("password") or "")
        env_name = str(p.get("password_env") or "DDP_PLATFORM_PASSWORD").strip() or "DDP_PLATFORM_PASSWORD"
        password = os.environ.get(env_name, password) or password
        return {
            "host": str(p.get("host") or "127.0.0.1"),
            "port": int(p.get("port") or 9030),
            "user": str(p.get("user") or "root"),
            "password": password,
            "database": str(p.get("database") or "").strip() or None,
            "connect_timeout": int(p.get("connect_timeout") or 10),
            "charset": "utf8mb4",
            "autocommit": True,
        }

    def _raw_execute(self, sql: str, env: Optional[str] = None) -> Dict[str, Any]:
        """Execute one SQL statement; returns evidence. Overridable in tests."""
        try:
            import pymysql  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on env
            raise clients.ClientError(
                f"{self.engine} 连接需要 pymysql 驱动：py -3 -m pip install pymysql（{exc}）"
            )
        started = time.time()
        params = self._params(env)
        conn = pymysql.connect(**params)
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                affected = int(getattr(cursor, "rowcount", 0) or 0)
        finally:
            conn.close()
        return self._receipt(sql, started, {"affected_rows": affected})

    def _receipt(self, sql: str, started: float, extra: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "SUCCESS",
            "query_id": "run_" + context.sha256_text(f"{sql}|{context.utcnow()}")[:14],
            "engine": self.engine,
            "evidence_level": engines.EVIDENCE_LOCAL,
            "executed_at": context.utcnow(),
            "duration_s": round(time.time() - started, 3),
            **extra,
        }

    def run_sql(self, sql: str, project: str = "dev", env: str = "dev") -> Dict[str, Any]:
        if not sql or not str(sql).strip():
            raise clients.ClientError("run_sql 需要非空 SQL")
        evidence = self._raw_execute(str(sql), env=env)
        evidence["env"] = env
        evidence["project"] = project
        return evidence

    def check_grammar(self, sql: str) -> Dict[str, Any]:
        if not sql or not str(sql).strip():
            return {"valid": False, "errors": ["空 SQL"]}
        explain = f"EXPLAIN {str(sql).strip().rstrip(';')}"
        try:
            self._raw_execute(explain, env=None)
        except clients.ClientError as exc:
            return {"valid": False, "errors": [str(exc)]}
        return {"valid": True, "errors": []}


class CommandPlatformClient(TaskLedgerMixin, clients.PlatformClient):
    """Command-run engines (Paimon/Flink/Spark/Trino/Presto or a bridge script)."""

    def __init__(self, config: Dict[str, Any], root: Path):
        self.config = config
        self.root = Path(root)
        platform = config.get("platform") or {}
        command = platform.get("command") or {}
        self.engine = str(command.get("engine") or platform.get("kind") or "command")
        self._run_argv = list(command.get("run") or [])
        self._check_argv = list(command.get("check") or [])
        self._timeout = int(command.get("timeout") or 600)
        self._ledger = context.ledger_dir(self.root) / "platform_tasks.json"

    def _effective_config(self, env: Optional[str] = None) -> Dict[str, Any]:
        p = self.config.get("platform") or {}
        base_env = str(p.get("base_env") or "dev")
        target = str(env or base_env)
        profiles = p.get("profiles") or {}
        if target in profiles:
            merged = {k: p.get(k) for k in ("host", "port", "user", "password", "password_env", "database", "connect_timeout")}
            merged.update(profiles.get(target) or {})
            return merged
        if target == base_env:
            return p
        raise clients.ClientError(
            f"{self.engine} 未配置环境 {target!r}：请在 platform.profiles.{target} 提供连接参数"
            "（base_env=" + base_env + "），防止把非目标环境的 SQL 打到错误数据库。"
        )

    def _execute_argv(self, argv: List[str], sql: str, label: str, env: Optional[str] = None) -> Dict[str, Any]:
        started = time.time()
        run_dir = context.ledger_dir(self.root) / "sql_runs"
        context.ensure_dir(run_dir)
        with tempfile.NamedTemporaryFile("w", suffix=".sql", dir=str(run_dir), delete=False, encoding="utf-8") as handle:
            handle.write(sql)
            sql_file = Path(handle.name)
        p = self._effective_config(env)
        placeholders = {
            "{file}": str(sql_file),
            "{sql}": sql,
            "{host}": str(p.get("host") or ""),
            "{port}": str(p.get("port") or ""),
            "{user}": str(p.get("user") or ""),
            "{database}": str(p.get("database") or ""),
        }
        rendered: List[str] = []
        for arg in argv:
            out = str(arg)
            for key, value in placeholders.items():
                out = out.replace(key, value)
            rendered.append(out)
        return self._run_command(rendered, sql, started, label, sql_file)

    def _run_command(
        self,
        argv: List[str],
        sql: str,
        started: float,
        label: str,
        sql_file: Path,
    ) -> Dict[str, Any]:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=self._timeout, check=False)
        except subprocess.TimeoutExpired:
            sql_file.unlink(missing_ok=True)
            raise clients.ClientError(f"{self.engine} 命令执行超时（>{self._timeout}s）")
        sql_file.unlink(missing_ok=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[-800:]
            raise clients.ClientError(f"{self.engine} {label} 退出码 {proc.returncode}: {err}")
        stdout_tail = (proc.stdout or "").strip()[-2000:]
        extra: Dict[str, Any] = {"rc": proc.returncode}
        if stdout_tail:
            extra["stdout_tail"] = stdout_tail
        return self._receipt(sql, started, extra)

    def _raw_execute(self, sql: str, env: Optional[str] = None) -> Dict[str, Any]:
        if not self._run_argv:
            raise clients.ClientError(
                f"{self.engine} 命令执行器未配置：platform.command.run（例如 ['flink','sql','-f','{{file}}']）"
            )
        return self._execute_argv(self._run_argv, sql, "执行", env=env)

    def _receipt(self, sql: str, started: float, extra: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "SUCCESS",
            "query_id": "run_" + context.sha256_text(f"{sql}|{context.utcnow()}")[:14],
            "engine": self.engine,
            "evidence_level": engines.EVIDENCE_LOCAL,
            "executed_at": context.utcnow(),
            "duration_s": round(time.time() - started, 3),
            **extra,
        }

    def run_sql(self, sql: str, project: str = "dev", env: str = "dev") -> Dict[str, Any]:
        if not sql or not str(sql).strip():
            raise clients.ClientError("run_sql 需要非空 SQL")
        evidence = self._raw_execute(str(sql), env=env)
        evidence["env"] = env
        evidence["project"] = project
        return evidence

    def check_grammar(self, sql: str) -> Dict[str, Any]:
        if not sql or not str(sql).strip():
            return {"valid": False, "errors": ["空 SQL"]}
        if not self._check_argv:
            return {"valid": True, "errors": [], "note": "命令模式未配置 check 命令，语法在真实执行时校验"}
        try:
            self._execute_argv(self._check_argv, sql, "语法检查", env=None)
        except clients.ClientError as exc:
            return {"valid": False, "errors": [str(exc)]}
        return {"valid": True, "errors": []}


def _date_range(start: str, end: str, max_days: int = 90) -> List[str]:
    from datetime import date, datetime, timedelta

    def parse(value: str) -> date:
        value = str(value).strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"无法解析日期: {value}")

    try:
        start_d, end_d = parse(start), parse(end)
    except ValueError:
        return []
    if start_d > end_d or (end_d - start_d).days + 1 > max_days:
        return []
    out: List[str] = []
    cur = start_d
    while cur <= end_d:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out
