#!/usr/bin/env python3
"""最小"任务单系统"JSON CLI（live 模式 cli.tracker 的契约实现）。

契约见 docs/REPRODUCTION-NOTES.md 1.1：create/show/list/comment/comments/set-status，
stdout 必须是 JSON。状态流不允许跨级：已提交 → 开发中 → 待验收 → 待测试 → 待业务验收 → 已交付。

它只是测试替身：证明插件的 live 通道真的会去调外部 CLI，而不是走 mock 分支。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parent / ".tracker.json"
FLOW = ["已提交", "开发中", "待验收", "待测试", "待业务验收", "已交付"]


def load() -> dict:
    if STORE.exists():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return {"items": {}, "seq": 0}


def save(data: dict) -> None:
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def arg(name: str, default: str = "") -> str:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> int:
    data = load()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "create":
        data["seq"] += 1
        tid = f"T-{data['seq']:04d}"
        item = {
            "id": tid,
            "title": arg("--title"),
            "description": arg("--description"),
            "type": arg("--templateTypeName", "数据任务"),
            "status": FLOW[0],
            "comments": [],
        }
        data["items"][tid] = item
        save(data)
        print(json.dumps(item, ensure_ascii=False))
        return 0

    tid = arg("--id")
    item = data["items"].get(tid)
    if item is None:
        print(json.dumps({"error": f"工单不存在: {tid}"}, ensure_ascii=False), file=sys.stderr)
        return 2

    if cmd == "show":
        print(json.dumps(item, ensure_ascii=False))
    elif cmd == "list":
        print(json.dumps({"items": list(data["items"].values())}, ensure_ascii=False))
    elif cmd == "comment":
        # 字段名按引擎消费的契约：anchor / content_md（mock 客户端同形）
        item["comments"].append({
            "id": f"c-{len(item['comments']) + 1:03d}",
            "anchor": arg("--anchor"),
            "content_md": arg("--content"),
        })
        save(data)
        print(json.dumps(item, ensure_ascii=False))
    elif cmd == "comments":
        print(json.dumps(item["comments"], ensure_ascii=False))
    elif cmd == "set-status":
        target = arg("--status")
        if target not in FLOW:
            print(json.dumps({"error": f"未知状态 {target}"}, ensure_ascii=False), file=sys.stderr)
            return 2
        if FLOW.index(target) > FLOW.index(item["status"]) + 1:
            print(json.dumps({"error": f"不允许跨级：{item['status']} → {target}"}, ensure_ascii=False), file=sys.stderr)
            return 2
        item["status"] = target
        item.setdefault("status_log", []).append({"to": target, "reason": arg("--reason")})
        save(data)
        print(json.dumps(item, ensure_ascii=False))
    else:
        print(json.dumps({"error": f"未知子命令 {cmd}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
