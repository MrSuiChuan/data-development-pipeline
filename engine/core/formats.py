"""湖表格式：Iceberg / Hudi / Paimon / Delta 的**差异与运维面**。

湖表不是"另一种数据库"，而是挂在执行引擎上的表格式：同一条 SQL 在 spark+iceberg 与
flink+paimon 上都能跑，但建表属性、写入语义、运维动作完全不同
（Hudi 要 primaryKey/preCombineField 才 upsert、Iceberg 分区演进是独立 DDL、
Delta 有 OPTIMIZE/VACUUM、Paimon 有 compaction 与 bucket 并发）。

本文件把这些差异写成数据：`FORMATS[fmt]` 给出建表片段（按方言）、写入提示、运维清单与检查项。
格式与执行引擎**正交**：能不能组合由注册表（`engines.EngineSpec.formats`）判定，
组合未登记就报错——不猜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

FAIL = "FAIL"
WARN = "WARN"


@dataclass(frozen=True)
class FormatCheck:
    id: str
    pattern: str
    severity: str
    message: str
    repair: str = ""

    def hits(self, sql: str) -> bool:
        return re.search(self.pattern, sql, re.IGNORECASE | re.MULTILINE) is not None


@dataclass(frozen=True)
class FormatSpec:
    name: str
    label: str
    # 建表时插进 DDL 的片段：方言 → 片段（ddl.py 负责与通用模板拼装）
    ddl_hint: str
    write_hint: str
    ops_notes: Tuple[str, ...] = ()
    checks: Tuple[FormatCheck, ...] = ()
    # 该格式支持的建表方言（与注册表交叉核对，不一致时以注册表为准并报警）
    dialects: Tuple[str, ...] = ()


def _c(id: str, pattern: str, message: str, repair: str = "", severity: str = WARN) -> FormatCheck:
    return FormatCheck(id=id, pattern=pattern, severity=severity, message=message, repair=repair)


FORMATS: Dict[str, FormatSpec] = {
    "iceberg": FormatSpec(
        name="iceberg",
        label="Apache Iceberg（湖表：快照 + 分区演进）",
        ddl_hint="USING iceberg",
        write_hint="整分区覆盖用 INSERT OVERWRITE ... PARTITION（动态分区）；"
                   "行级更新用 MERGE INTO（需要引擎版本与 catalog 支持）",
        ops_notes=(
            "快照会累积：定期 expire_snapshots，否则元数据与存储持续膨胀",
            "分区演进用 ALTER TABLE ... ADD/DROP PARTITION FIELD（不会重写历史数据）",
            "小文件用 rewrite_data_files / OPTIMIZE（按引擎）",
        ),
        checks=(
            _c("format.iceberg.row_delete", r"\bDELETE\s+FROM\b",
               "Iceberg 的行级 DELETE 依赖引擎与格式版本（Spark 3.x + Iceberg 0.14+），先确认再依赖",
               "不确定就改成按分区 INSERT OVERWRITE"),
            _c("format.iceberg.drop_partition", r"\bALTER\s+TABLE\b[\s\S]{0,120}?\bDROP\s+PARTITION\b",
               "Iceberg 的 DROP PARTITION 走分区演进语法，不是 Hive 风格的 DROP PARTITION",
               "用 ALTER TABLE ... DROP PARTITION FIELD，并确认历史数据保留策略"),
        ),
        dialects=("spark", "trino", "flink"),
    ),
    "hudi": FormatSpec(
        name="hudi",
        label="Apache Hudi（湖表：主键 upsert + MOR/COW）",
        ddl_hint="USING hudi",
        write_hint="upsert 依赖主键与预合并字段：INSERT INTO 走主键去重，MERGE INTO 需要 primaryKey",
        ops_notes=(
            "MOR 表要跑 compaction，否则读放大会持续变差",
            "cleaning（清理旧文件版本）与归档是独立运维动作",
            "增量读依赖 timeline，不是普通全表扫",
        ),
        checks=(
            _c("format.hudi.missing_primary_key",
               r"\bUSING\s+hudi\b(?![^;]*\bprimaryKey\b)",
               "Hudi 建表没写 primaryKey：upsert / MOR 语义不成立（只有 append-only 表例外）",
               "补 TBLPROPERTIES ('primaryKey'='...','preCombineField'='...')"),
            _c("format.hudi.overwrite_table", r"\bINSERT\s+OVERWRITE\b(?![^;]*\bPARTITION\b)",
               "Hudi 整表覆盖会绕开 upsert 语义（并影响 timeline），分区表请显式 PARTITION",
               "分区覆盖或改用 INSERT INTO + 主键"),
        ),
        dialects=("spark", "trino", "flink"),
    ),
    "paimon": FormatSpec(
        name="paimon",
        label="Apache Paimon（湖表：主键表 + LSM）",
        ddl_hint="WITH ('connector'='paimon')",
        write_hint="主键表用 INSERT INTO 做 upsert；整表/分区覆盖用 INSERT OVERWRITE",
        ops_notes=(
            "compaction 决定读性能（主键表尤其），要么自动要么显式触发",
            "快照过期（snapshot expire）与 changelog 保留策略要按下游消费需求定",
            "bucket 数影响写并发与并行度，建表时定比事后改省事",
        ),
        checks=(
            _c("format.paimon.missing_bucket", r"\bconnector\b'?\s*=\s*'paimon'(?![^;]*\bbucket\b)",
               "Paimon 建表没显式 bucket：默认值可能不适合写入并发",
               "补 'bucket' = '<N>'（按数据量定），并在交付说明里写明理由"),
        ),
        dialects=("flink", "spark"),
    ),
    "delta": FormatSpec(
        name="delta",
        label="Delta Lake（湖表：事务日志 + MERGE）",
        ddl_hint="USING delta",
        write_hint="MERGE INTO 是主路径；INSERT OVERWRITE 支持整表/分区替换",
        ops_notes=(
            "OPTIMIZE 合并小文件、Z-ORDER 优化查询，但要单独调度",
            "VACUUM 会物理删除超出保留期的旧文件（不可回滚），保留期与并发读要一起评估",
            "schema 演进需要 overwriteSchema / mergeSchema 显式开启",
        ),
        checks=(
            _c("format.delta.vacuum", r"^\s*VACUUM\b",
               "VACUUM 不可回滚地删除历史文件：确认保留期与并发读（含流读）需要",
               "默认保留 7 天，缩短保留期前先确认没有长跑读任务"),
            _c("format.delta.overwrite_schema", r"\bINSERT\s+OVERWRITE\b(?![^;]*\boverwriteSchema\b)",
               "Delta 的 INSERT OVERWRITE 默认不改变 schema；要改结构需显式 overwriteSchema",
               "加 OPTIONS ('overwriteSchema'='true') 或先 ALTER 列"),
        ),
        dialects=("spark", "trino"),
    ),
}


def available_formats() -> Tuple[str, ...]:
    return ("none",) + tuple(sorted(FORMATS))


def resolve(name: str) -> Optional[FormatSpec]:
    key = str(name or "none").strip().lower()
    if key in ("", "none"):
        return None
    if key not in FORMATS:
        raise ValueError(
            f"未知表格式 {name!r}。可用: {', '.join(available_formats())}"
            "（要加格式请同时补 engine/core/formats.py 与注册表里的 kind.formats）"
        )
    return FORMATS[key]


def format_issues(sql: str, name: str) -> List[Dict[str, object]]:
    """按表格式检查一段 SQL（只在显式配置/指定 format 时生效）。"""
    spec = resolve(name)
    if spec is None:
        return []
    out: List[Dict[str, object]] = []
    for check in spec.checks:
        if check.hits(sql):
            out.append(
                {
                    "id": check.id,
                    "severity": check.severity,
                    "message": check.message,
                    "repair": check.repair,
                    "format": spec.name,
                }
            )
    return out


def guidance(name: str) -> List[str]:
    """写入提示 + 运维清单（挂进渲染结果的 warnings，跟着产物走）。"""
    spec = resolve(name)
    if spec is None:
        return []
    return [f"{spec.label} 写入：{spec.write_hint}"] + [f"{spec.label} 运维：{note}" for note in spec.ops_notes]


def validate_registry() -> List[str]:
    problems: List[str] = []
    for name, spec in FORMATS.items():
        if name != spec.name:
            problems.append(f"{name}: 键名与 spec.name({spec.name}) 不一致")
        if not spec.ddl_hint:
            problems.append(f"{name}: 缺 ddl_hint")
        for check in spec.checks:
            if check.severity not in (FAIL, WARN):
                problems.append(f"{check.id}: 未知 severity {check.severity!r}")
            try:
                re.compile(check.pattern, re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                problems.append(f"{check.id}: 正则非法（{exc}）")
        if not spec.dialects:
            problems.append(f"{name}: 缺 dialects")
    return problems
