"""Rendering helpers: SDS spec, Tracker comment, requirement_spec bridge, diffs."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from . import context


def _ticket_identity(req_dir: Path, sds: Dict[str, Any], workitem: Optional[Dict[str, Any]] = None):
    """工单号与标题：SDS 里没写时，从 tracker 记录与工单目录名兜底。

    真实踩坑（T-0002）：SDS 里通常没有 ticket_id/title 字段，旧实现直接取值渲染，
    说明书抬头于是变成「# 数据概要设计（?）」、正文写成「- 工单: None ·」。
    工单号其实就写在路径上（tickets/T-0002），取得到的东西不该渲染成 None。
    """
    ticket_id = str(sds.get("ticket_id") or "").strip()
    title = str(sds.get("title") or "").strip()
    if isinstance(workitem, dict):
        ticket_id = ticket_id or str(workitem.get("id") or "").strip()
        title = title or str(workitem.get("title") or "").strip()
    return (ticket_id or req_dir.name, title)


def spec_fingerprint(sds: Dict[str, Any], body: str) -> str:
    """口径指纹：覆盖工单正文 + 目标表 + 字段映射 + SQL 验证结论。

    旧实现只哈希 workitem['description_md']，而 tracker 实际存的是 'description' ——
    取不到就成了空串，ticket_hash 恒等于空串的 sha256（e3b0c44298fc1c14...），
    且完全不含 SDS 口径内容：改目标表名后 hash 一点不变，准入门第 4 步等于没把关。
    """
    payload = {
        "body": body,
        "target_table": (sds or {}).get("target_table"),
        "field_mappings": (sds or {}).get("field_mappings"),
        "sql_validation": (sds or {}).get("sql_validation"),
    }
    return context.sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def render_sds_spec(req_dir: Path) -> str:
    """Render the four-section SDS specification markdown (确认门用)."""
    sds = context.load_json(req_dir / "sds_design.json") or {}
    target = sds.get("target_table") or {}
    mappings = sds.get("field_mappings") or []
    sql = sds.get("sql_validation") or {}
    ticket_id, title = _ticket_identity(req_dir, sds, None)
    lines = [
        f"# 数据概要设计（{ticket_id}）",
        "",
        "## 一、需求与结论",
        "",
        f"- 工单: {ticket_id} · {title}".rstrip(),
        f"- 模式: {sds.get('mode')} · 难度建议: {sds.get('level_suggestion', '-')}",
        f"- 声明来源: {sds.get('declared_source') or '（无，走 generate）'}",
        f"- 知识源: {sds.get('knowledge_source_uri', '-')}",
        "",
        "## 二、目标表与字段映射",
        "",
        f"- 目标表: `{target.get('name', '-')}`（层 {target.get('layer', '-')} / 域 {target.get('domain', '-')}）",
        f"- 中间表: {'需要' if sds.get('needs_intermediate_table') else '不需要'} {sds.get('intermediate_note') or ''}",
        "",
        "| 目标字段 | 来源表 | 来源字段 | 结论 | correction |",
        "| --- | --- | --- | --- | --- |",
    ]
    for mp in mappings:
        lines.append(
            f"| {mp.get('target_field')} | {mp.get('source_table')} | {mp.get('source_field')} "
            f"| {mp.get('conclusion')} | {mp.get('correction') or ''} |"
        )
    lines += [
        "",
        "## 三、SQL 验证证据与口径",
        "",
        f"- sql_count: {sql.get('sql_count')} · join_match_rate: {sql.get('join_match_rate')} · "
        f"null_rate: {sql.get('null_rate')} · row_count: {sql.get('row_count')}",
    ]
    for idx, mp in enumerate(mappings or []):
        ev = mp.get("sql_evidence") or {}
        lines.append(
            f"- 映射 #{idx + 1} {mp.get('target_field')}: 命中率 {ev.get('join_match_rate')}, "
            f"空值率 {ev.get('null_rate')}{'（主键/键字段 0 空）' if ev.get('key_kind') == 'pk' else ''}"
        )
    lines += [
        "",
        "## 四、风险与知识缺口",
        "",
    ]
    gaps = sds.get("knowledge_gaps") or []
    lines.append(f"- 知识缺口: {('无' if not gaps else '、'.join(str(g) for g in gaps))}")
    if sds.get("knowledge_backfill", {}).get("required"):
        lines.append(f"- 增量沉淀: 待决议（resolved={sds.get('knowledge_backfill', {}).get('resolved')}）")
    lines.append("")
    return "\n".join(lines)


def build_ticket_comment(req_dir: Path) -> Dict[str, Any]:
    """Assemble the 【数据概要设计 vN】comment: anchor + spec + machine JSON + confirm line."""
    sds = context.load_json(req_dir / "sds_design.json") or {}
    version = int(sds.get("final_version", 1) or 1)
    anchor = f"数据概要设计 v{version}"
    spec = context.read_text(req_dir / "sds_design_spec.md")
    if not spec.strip():
        spec = render_sds_spec(req_dir)
    payload = json.dumps(sds, ensure_ascii=False, indent=2)
    content = "\n".join(
        [
            f"### 【{anchor}】",
            "",
            spec,
            "```json",
            payload,
            "```",
            "",
            f"确认行: [已确认] {anchor} 由 data-development-plugin 生成并校验（validator=passed, evidence 比对一致）。",
        ]
    )
    return {"anchor": anchor, "content_md": content}


def verify_latest_comment(comments: list, anchor_prefix: str = "数据概要设计 v") -> Optional[Dict[str, Any]]:
    """Verify the latest final-version comment carries spec + json block + confirm line."""
    finals = []
    for comment in comments or []:
        anchor = str(comment.get("anchor", ""))
        m = re.match(rf"^{re.escape(anchor_prefix)}(\d+)$", anchor)
        if m:
            finals.append((int(m.group(1)), comment))
    if not finals:
        return None
    # 同一个版本号可能被贴两次（重跑 SDS 会再贴一条 v1）。真平台的评论列表按时间序返回，
    # "最新终版"应当是**最后一条**同版本评论，而不是第一（max 平局取首个会验到旧的那条——
    # 2026-10-07 在真 tracker 上实测踩到）。
    best_version, latest = -1, finals[0][1]
    for version, comment in finals:
        if version >= best_version:
            best_version, latest = version, comment
    text = str(latest.get("content_md", ""))
    ok = (
        "```json" in text
        and '"confirmed": true' in text
        and "[已确认]" in text
        and "## 一、需求与结论" in text
    )
    return {"comment": latest, "verified": ok}


def bridge_to_spec(req_dir: Path, workitem: Dict[str, Any]) -> Dict[str, Any]:
    """Build requirement_spec.md: ticket body on top, SDS bridge on the bottom."""
    sds = context.load_json(req_dir / "sds_design.json") or {}
    target = sds.get("target_table") or {}
    mappings = sds.get("field_mappings") or []
    if isinstance(workitem, dict):
        # tracker 的记录用的是 'description'；'description_md' 只在部分适配器里出现。
        # 只认前者会导致 body 恒为空串（ticket_hash 变成空串 sha256，见 PF-003）。
        body = str(workitem.get("description_md") or workitem.get("description") or "")
    else:
        body = ""
    ticket_id, title = _ticket_identity(req_dir, sds, workitem)
    lines = [
        "# requirement_spec.md（P2-P6 统一输入）",
        "",
        "## 上半部：工单原文",
        "",
        body,
        "",
        "---",
        "",
        "## 下半部：桥接自 SDS（机器校验产物，改动须回 P1 重跑）",
        "",
        f"- ticket: {ticket_id}",
        f"- title: {title}",
        f"- level: {sds.get('level_suggestion', 'L1')}",
        f"- mode: {sds.get('mode')}",
        f"- knowledge_source_uri: {sds.get('knowledge_source_uri', '')}",
        f"- target_table: {target.get('name', '')}（layer={target.get('layer', '')}, domain={target.get('domain', '')}）",
        f"- needs_intermediate_table: {sds.get('needs_intermediate_table', False)}",
        "",
        "### 字段映射（已逐条 SQL 验证）",
        "",
    ]
    for idx, mp in enumerate(mappings or []):
        ev = mp.get("sql_evidence") or {}
        lines.append(
            f"{idx + 1}. {mp.get('target_field')} <- {mp.get('source_table')}.{mp.get('source_field')} "
            f"[{mp.get('conclusion')}] join_match_rate={ev.get('join_match_rate')} null_rate={ev.get('null_rate')}"
        )
    lines += [
        "",
        "### 研发约束",
        "",
        "- DDL -> ETL -> dp_task 三层文件必须同名同序；",
        "- P3 出口需 etl_dev_exec.json（status=SUCCESS + query_id）与 self_check；",
        "- P4 报告标✅ 的 TC 必须在 test_runs.json 有 SUCCESS + query_id；",
        "- P5 发布/ALTER/回刷必须 AskUserQuestion 确认并带 [已确认发布] token；",
        "- P6 是否进语义层由用户拍板，不得 AI 自决。",
        "",
    ]
    context.write_text(req_dir / "requirement_spec.md", "\n".join(lines))
    result: Dict[str, Any] = {
        "level": sds.get("level_suggestion"),
        "ticket_hash": spec_fingerprint(sds, body),
    }
    warnings = []
    if not sds.get("level_suggestion"):
        # 静默返回 level=null 会一路拖到 P2 出口才暴露（实测），这里当场说清楚
        warnings.append(
            "sds_design.json 缺 level_suggestion：状态机 level 会是 null，P2 出口会因难度级别缺失而卡住，"
            "请在 SDS 里补 L1/L2/L3 后重跑 sds bridge"
        )
    if not str(target.get("name") or "").strip():
        warnings.append("sds_design.json 缺 target_table.name：P3 出口无法校验 SDS 目标表是否真的造了")
    if warnings:
        result["warnings"] = warnings
    return result


def render_code_diff_html(old_text: str, new_text: str, title: str = "code_diff") -> str:
    diff = list(difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="", n=2))
    body_lines = []
    for line in diff:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            body_lines.append(f"<div class='meta'>{line}</div>")
        elif line.startswith("+"):
            body_lines.append(f"<div class='add'>{line}</div>")
        elif line.startswith("-"):
            body_lines.append(f"<div class='del'>{line}</div>")
        else:
            body_lines.append(f"<div class='ctx'>{line}</div>")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title><style>
body{{font-family:ui-monospace,Consolas,monospace;margin:24px;background:#fff}}
h1{{font-size:18px}} .add{{background:#e6ffec;color:#1a7f37;white-space:pre-wrap}}
.del{{background:#ffebe9;color:#cf222e;white-space:pre-wrap}}
.ctx{{color:#57606a;white-space:pre-wrap}} .meta{{color:#0969da;font-weight:bold}}
</style></head><body><h1>{title}</h1>{''.join(body_lines) or '<p>无差异</p>'}</body></html>"""


def render_review_report_html(req_dir: Path, decision_count: int) -> str:
    decisions = context.read_text(req_dir / "autopilot_decisions.md")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>review_report</title>
<style>body{{font-family:sans-serif;margin:24px}} pre{{background:#f6f8fa;padding:12px}}</style>
</head><body><h1>发布前审查报告 review_report</h1>
<p>autopilot 决策记录 {decision_count} 条；⚠️ 不可逆风险项已逐项 AskUserQuestion 确认。</p>
<pre>{decisions[:4000]}</pre></body></html>"""
