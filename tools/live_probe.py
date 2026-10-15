#!/usr/bin/env python3
"""把"一次真跑"固化成可复现脚本：doctor → ddl render → 建表 → 写入 → 读回 → 证据核验。

背景：LIVE-001…005 这七个组件是我手工一遍遍跑出来的，每组件的命令都在 `.tmp` 里各写一份。
这个脚本把**引擎无关的那部分**收成一条命令——它只调用插件自己的引擎（`engine/ddp.py`），
所以对 dbapi / command / cli 各族都成立；引擎特有的东西（argv、镜像、端口、湖表 catalog）
留在项目配置里，由 `docs/engines/lab.md` 的配方给出。

用法：

    py -3 tools/live_probe.py --project .tmp/live-ch/proj --table ods_orders_live --spec spec.json
    py -3 tools/live_probe.py --project ... --dry-run         # 只打印将要执行的命令
    py -3 tools/live_probe.py --project ... --stop-container ddp-ch   # 跑完停容器

它**不**替你判断"这个引擎该配成什么样"：配置不对时它会原样把引擎的报错打出来。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "engine" / "ddp.py"
TICKET = "T-LIVE"


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def ddp_command(project: Path, *args: str) -> list[str]:
    return [sys.executable, str(ENGINE), "--root", str(project), *args]


def build_steps(project: Path, spec_path: Path, table: str, insert_sql: str, readback_sql: str) -> list[dict]:
    req = project / ".data-dev" / "tickets" / TICKET
    ddl = req / "ddl" / f"{table}.sql"
    etl = req / "etl" / f"{table}.sql"
    return [
        {"name": "doctor", "argv": ddp_command(project, "platform", "doctor")},
        {"name": "ddl render", "argv": ddp_command(project, "ddl", "render", "--spec", str(spec_path), "--out", str(ddl))},
        {"name": "建表（真跑）", "argv": ddp_command(project, "platform", "run-sql", "--file", str(ddl),
                                                    "--env", "dev", "--save-evidence", "--ticket", TICKET)},
        {"name": "写 etl 文件", "write": {"path": str(etl), "content": insert_sql}},
        {"name": "写入（真跑）", "argv": ddp_command(project, "platform", "run-sql", "--file", str(etl),
                                                    "--env", "dev", "--save-evidence", "--ticket", TICKET)},
        # 读回走同一条执行器（命令族的回执里带 stdout_tail，能看到查询结果）
        {"name": "读回（真实查询）", "argv": ddp_command(project, "platform", "run-sql", "--sql", readback_sql, "--env", "dev")},
        {"name": "evidence verify", "argv": ddp_command(project, "evidence", "verify", "--ticket", TICKET)},
    ]


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(prog="live_probe", description="一次真跑的固定流程（引擎无关部分）")
    parser.add_argument("--project", required=True, help="DDP 项目根（含 .data-dev/）")
    parser.add_argument("--table", required=True, help="目标表名（可带 catalog/db 前缀，用末段做文件名）")
    parser.add_argument("--spec", required=True, help="建表 spec.json")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不真的跑")
    parser.add_argument("--stop-container", default="", help="跑完顺手 docker stop 这个容器（可多次传）")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    spec_path = Path(args.spec).expanduser().resolve()
    stem = str(args.table).split(".")[-1]
    insert_sql = (
        f"-- table: {args.table}\n-- source: {stem}_raw\n-- schedule: 0 2 * * *\n-- env: dev\n"
        f"INSERT INTO {args.table} (order_id, amt, ds) VALUES\n"
        f"  (1, 10.50, DATE '2026-09-28'),\n  (2, 20.25, DATE '2026-09-28'),\n  (3, 30.00, DATE '2026-09-28');\n"
    )
    readback_sql = f"SELECT count(*) AS rows_inserted, sum(amt) AS amt_sum FROM {args.table}"
    steps = build_steps(project, spec_path, stem, insert_sql, readback_sql)

    if args.dry_run:
        for step in steps:
            if "write" in step:
                print(f"[dry-run] 写文件 {step['write']['path']}")
            else:
                print("[dry-run] " + " ".join(step["argv"]))
        if args.stop_container:
            for name in args.stop_container.split(","):
                print(f"[dry-run] docker stop {name.strip()}")
        return 0

    for step in steps:
        if "write" in step:
            target = Path(step["write"]["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            # 同 build_codex_surface：write_text(newline=) 是 3.10+，本仓库下限是 3.9
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(step["write"]["content"])
            print(f"=== {step['name']}")
            continue
        print(f"=== {step['name']}")
        proc = subprocess.run(step["argv"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        if proc.returncode != 0:
            print(f"[失败] {step['name']} 退出码 {proc.returncode}")
            return proc.returncode

    if args.stop_container:
        for name in args.stop_container.split(","):
            subprocess.run(["docker", "stop", name.strip()], capture_output=True)
            print(f"已停止容器 {name.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
