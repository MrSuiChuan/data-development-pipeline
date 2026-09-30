"""按方言的 SQL 规则：17 项基线之外的"这个引擎不吃这一口"。

基线 17 项（validators.sql_compliance_check）问的是"离线任务的通用纪律"：
目标表是否一致、周期任务是否偷偷 ALTER、写表前有没有建表……这些跨引擎都成立。

本文件回答另一个问题：**同一条 SQL 在这个引擎上语义是否成立**。例如同样一句
`DELETE FROM t WHERE ds='...'`，在 Doris 主键表上合法、在 Hive 非 ACID 表上根本跑不了、
在 MaxCompute 上要改成 INSERT OVERWRITE 重写分区。这类差异写成数据（而不是散在文档里），
由 `dialect_issues()` 统一产出，severity 与修复提示都跟着规则走。

设计约定：

* 规则是**纯数据**：id / dialect / pattern / severity / message / repair；
* 规则只在显式给出 dialect 时生效——不给 dialect 时行为与改动前完全一致（回归锁）；
* severity 只有 FAIL（明确写错）与 WARN（条件成立才错，需人确认）两种，
  拿不准的一律 WARN，不用 FAIL 冒充确定性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

FAIL = "FAIL"
WARN = "WARN"


@dataclass(frozen=True)
class DialectRule:
    id: str
    dialect: str
    pattern: str
    severity: str
    message: str
    repair: str = ""
    rule: int = 0  # 0 = 方言新增；1..17 = 基线里的对应项在本方言下的变体

    def hits(self, text: str) -> bool:
        return re.search(self.pattern, text, re.IGNORECASE | re.MULTILINE) is not None


def _r(**kwargs) -> DialectRule:
    return DialectRule(**kwargs)


RULES: Tuple[DialectRule, ...] = (
    # --- Hive -----------------------------------------------------------------
    _r(id="dialect.hive.no_row_write", dialect="hive",
       pattern=r"\b(UPDATE|DELETE)\b",
       severity=FAIL, rule=3,
       message="Hive 非 ACID 表不支持 UPDATE/DELETE（行级写）",
       repair="改成 INSERT OVERWRITE 重写目标分区；确实是事务表才保留行级写并注明表属性"),
    _r(id="dialect.hive.primary_key", dialect="hive",
       pattern=r"\bPRIMARY\s+KEY\b",
       severity=WARN,
       message="Hive 不支持 PRIMARY KEY 约束（只有 ACID 事务表通过 TBLPROPERTIES 开启）",
       repair="去掉约束，用去重逻辑或 INSERT OVERWRITE 保证唯一性"),
    _r(id="dialect.hive.merge_into", dialect="hive",
       pattern=r"\bMERGE\s+INTO\b",
       severity=WARN,
       message="MERGE 仅 Hive 4 事务表支持，低版本会直接语法报错",
       repair="确认版本与表属性；否则拆成 INSERT OVERWRITE"),

    # --- ClickHouse -----------------------------------------------------------
    _r(id="dialect.clickhouse.update_from", dialect="clickhouse",
       pattern=r"\bUPDATE\b[\s\S]{0,200}?\bFROM\b",
       severity=FAIL, rule=3,
       message="ClickHouse 不支持 UPDATE ... FROM 语法",
       repair="用 ALTER TABLE ... UPDATE（mutation），或按分区重建数据"),
    _r(id="dialect.clickhouse.mutation_async", dialect="clickhouse",
       pattern=r"\bALTER\s+TABLE\b[\s\S]{0,120}?\b(UPDATE|DELETE)\b",
       severity=WARN,
       message="ALTER ... UPDATE/DELETE 是异步 mutation：不等待就返回，且大表开销高",
       repair="设 mutations_sync=1 或轮询 system.mutations；大数据量优先重建分区"),
    _r(id="dialect.clickhouse.engine_required", dialect="clickhouse",
       pattern=r"\bCREATE\s+TABLE\b(?![^;]*\bENGINE\b)(?![^;]*\bAS\s+SELECT\b)",
       severity=WARN,
       message="ClickHouse 建表通常要显式 ENGINE（MergeTree 还需 ORDER BY）",
       repair="补 ENGINE 与 ORDER BY / PARTITION BY；CTAS 场景可忽略"),

    # --- Trino / Presto -------------------------------------------------------
    _r(id="dialect.trino.no_insert_overwrite", dialect="trino",
       pattern=r"\bINSERT\s+OVERWRITE\b",
       severity=FAIL,
       message="Trino/Presto 没有 INSERT OVERWRITE 语句",
       repair="用 DELETE + INSERT，或 connector 自己的 replace/CTAS 能力"),
    _r(id="dialect.trino.row_write_connector", dialect="trino",
       pattern=r"\b(UPDATE|DELETE)\b",
       severity=WARN, rule=3,
       message="Trino 的行级写依赖 connector（Hive connector 仅 ACID 表支持）",
       repair="确认目标表的 connector 能力，否则改成整分区覆盖"),

    # --- Spark SQL ------------------------------------------------------------
    _r(id="dialect.spark.row_write_needs_lakehouse", dialect="spark",
       pattern=r"\b(DELETE|UPDATE|MERGE)\b",
       severity=WARN, rule=3,
       message="Spark 的行级写只在 Delta / Hudi / Iceberg 或 ACID 表上成立，普通 Parquet/Hive 表不支持",
       repair="确认表格式；纯文件表请用 INSERT OVERWRITE 整分区重写"),
    _r(id="dialect.spark.overwrite_without_partition", dialect="spark",
       pattern=r"\bINSERT\s+OVERWRITE\b(?![^;]*\bPARTITION\b)",
       severity=WARN,
       message="INSERT OVERWRITE 不带 PARTITION 会覆盖整表（分区表尤其危险）",
       repair="分区表显式写 PARTITION (ds=...)"),

    # --- Flink SQL ------------------------------------------------------------
    _r(id="dialect.flink.no_row_write", dialect="flink",
       pattern=r"^\s*(UPDATE|DELETE)\b",
       severity=FAIL, rule=3,
       message="Flink SQL 不支持 UPDATE/DELETE 行级语句",
       repair="用 changelog 源 + upsert sink（kafka/paimon/jdbc）表达更新语义"),
    _r(id="dialect.flink.primary_key_needs_upsert", dialect="flink",
       pattern=r"\bPRIMARY\s+KEY\b",
       severity=WARN,
       message="Flink 的 PRIMARY KEY 只在支持 upsert 的 connector 上生效",
       repair="确认 sink connector 支持 upsert；否则请在写入端去重"),

    # --- MaxCompute / ODPS ----------------------------------------------------
    _r(id="dialect.odps.no_row_write", dialect="odps",
       pattern=r"^\s*(UPDATE|DELETE)\b",
       severity=FAIL, rule=3,
       message="MaxCompute 不支持行级 UPDATE/DELETE（事务表除外）",
       repair="改成 INSERT OVERWRITE 重写分区"),
    _r(id="dialect.odps.overwrite_without_partition", dialect="odps",
       pattern=r"\bINSERT\s+OVERWRITE\b(?![^;]*\bPARTITION\b)",
       severity=WARN,
       message="分区表 INSERT OVERWRITE 建议显式指定 PARTITION",
       repair="补 PARTITION (ds=...)；整表覆盖请写进交付说明"),

    # --- PostgreSQL / Greenplum ----------------------------------------------
    _r(id="dialect.postgres.alter_type_rewrite", dialect="postgres",
       pattern=r"\bALTER\s+TABLE\b[\s\S]{0,160}?\bTYPE\b",
       severity=WARN,
       message="ALTER TABLE ... TYPE 会重写整表并持有 ACCESS EXCLUSIVE 锁",
       repair="大表用新列 + 批量回填 + 改名，避免一次重写"),
    _r(id="dialect.postgres.truncate", dialect="postgres",
       pattern=r"^\s*TRUNCATE\b",
       severity=WARN,
       message="TRUNCATE 立即清空且不在事务外回滚；离线重跑优先分区替换",
       repair="改成按分区 DELETE/INSERT 或确认这是有意的全表重刷"),

    # --- MySQL 协议族（不含 Doris/StarRocks：它们有 INSERT OVERWRITE）--------
    _r(id="dialect.mysql.no_insert_overwrite", dialect="mysql",
       pattern=r"\bINSERT\s+OVERWRITE\b",
       severity=FAIL,
       message="MySQL 协议族没有 INSERT OVERWRITE（Doris/StarRocks 有）",
       repair="用 REPLACE INTO / INSERT ... ON DUPLICATE KEY UPDATE，或 DELETE + INSERT"),

    # --- Doris / StarRocks ----------------------------------------------------
    _r(id="dialect.doris.row_write_needs_key_model", dialect="doris",
       pattern=r"\b(UPDATE|DELETE)\b",
       severity=WARN, rule=3,
       message="Doris 的 UPDATE/DELETE 只支持主键/唯一模型，聚合与明细模型不支持"
               "（实测：UNIQUE KEY 表 UPDATE 成功；DUPLICATE KEY 表报 Only unique table could be updated）",
       repair="确认表模型；否则整分区 INSERT OVERWRITE 重写"),
    _r(id="dialect.starrocks.row_write_needs_key_model", dialect="starrocks",
       pattern=r"\b(UPDATE|DELETE)\b",
       severity=WARN, rule=3,
       message="StarRocks 的 UPDATE 需要主键模型：UNIQUE KEY 表实测不支持 UPDATE"
               "（table does not support update），DELETE 在唯一/主键模型上可用，明细/聚合模型都不行",
       repair="确认表模型；否则整分区 INSERT OVERWRITE 重写"),
)

BY_ID: Dict[str, DialectRule] = {rule.id: rule for rule in RULES}


def dialects() -> Tuple[str, ...]:
    return tuple(sorted({rule.dialect for rule in RULES}))


def rules_for(dialect: str) -> Tuple[DialectRule, ...]:
    target = str(dialect or "").strip().lower()
    return tuple(rule for rule in RULES if rule.dialect == target)


def dialect_issues(text: str, dialect: Optional[str], filename: str = "") -> List[Dict[str, object]]:
    """返回该方言下命中的规则（纯 dict，由 validators 转成 Issue）。

    DDL 文件的特殊性：`ddl/*.sql` 里出现 PRIMARY KEY 是常态（建表约束），
    Hive 的那条 WARN 仍应提示，但不应升级成 FAIL——所以 Hive 的规则本身就是 WARN。
    """
    if not dialect:
        return []
    out: List[Dict[str, object]] = []
    for rule in rules_for(dialect):
        if rule.hits(text):
            out.append(
                {
                    "id": rule.id,
                    "severity": rule.severity,
                    "message": rule.message,
                    "repair": rule.repair,
                    "rule": rule.rule,
                    "dialect": rule.dialect,
                    "file": filename,
                }
            )
    return out


def validate_rules() -> List[str]:
    """规则表自检：结构错误在测试里就炸。"""
    problems: List[str] = []
    seen: Dict[str, str] = {}
    for rule in RULES:
        if rule.id in seen:
            problems.append(f"规则 id 重复：{rule.id}")
        seen[rule.id] = rule.dialect
        if rule.severity not in (FAIL, WARN):
            problems.append(f"{rule.id}: 未知 severity {rule.severity!r}")
        if not rule.message:
            problems.append(f"{rule.id}: 缺 message")
        if not rule.dialect:
            problems.append(f"{rule.id}: 缺 dialect")
        if rule.rule not in range(0, 18):
            problems.append(f"{rule.id}: rule 必须在 0..17")
        try:
            re.compile(rule.pattern, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            problems.append(f"{rule.id}: 正则非法（{exc}）")
    return problems
