"""类型映射与 DDL 渲染：canonical 类型 → 各引擎物理类型，并按引擎渲染 CREATE TABLE。

为什么需要它：同一张表在 Doris 是 `DECIMALV3(18,2)`、Hive 是 `DECIMAL(18,2)`、
ClickHouse 是 `Decimal(18,2)`、PostgreSQL 是 `NUMERIC(18,2)`，靠人手抄的后果是
"运行期才炸"，而且精度截断这类错**根本不报错**。

边界（写在前面，免得被当成 DDL 生成器）：

* 渲染出来的是**符合该引擎语法的起始模板**，不是"能直接上线的建表语句"；
* 引擎专有决策（表模型、分布键、分桶数、存储格式、connector 参数）由人拍板，
  走 `spec.properties` 原样透传，插件不猜；
* 渲染结果照样要过 P3 的 `sql check` 与真实执行——渲染通过 ≠ 建表成功。

映射表（``TYPE_MAP``）是唯一权威：`ddp.py ddl render` 与类型合法性校验都从这里取值；
未知 canonical 类型或未登记引擎一律报错，不做"差不多就行"的近似映射。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import engines, formats

# canonical 类型：够表达离线数仓的常规列，且每个都能映到所有已登记引擎
CANONICAL_TYPES: Tuple[str, ...] = (
    "string", "bigint", "int", "double", "decimal", "boolean", "date", "timestamp",
)

# canonical → 各方言物理类型。key 必须是 engines 里出现过的 dialect；
# decimal/timestamp 支持参数（precision/scale、精度），用 {precision}/{scale} 占位。
TYPE_MAP: Dict[str, Dict[str, str]] = {
    "mysql": {
        "string": "VARCHAR(255)", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "TINYINT(1)",
        "date": "DATE", "timestamp": "DATETIME",
    },
    "doris": {
        "string": "VARCHAR(255)", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMALV3({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "DATETIME",
    },
    "starrocks": {
        "string": "VARCHAR(255)", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "DATETIME",
    },
    "hive": {
        "string": "STRING", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP",
    },
    "spark": {
        "string": "STRING", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP",
    },
    "flink": {
        "string": "STRING", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP(3)",
    },
    "trino": {
        "string": "VARCHAR", "bigint": "BIGINT", "int": "INTEGER", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP",
    },
    "clickhouse": {
        "string": "String", "bigint": "Int64", "int": "Int32", "double": "Float64",
        "decimal": "Decimal({precision}, {scale})", "boolean": "Bool",
        "date": "Date", "timestamp": "DateTime64(3)",
    },
    "postgres": {
        "string": "TEXT", "bigint": "BIGINT", "int": "INTEGER", "double": "DOUBLE PRECISION",
        "decimal": "NUMERIC({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP",
    },
    "odps": {
        "string": "STRING", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "DATETIME",
    },
    "generic": {
        "string": "VARCHAR(255)", "bigint": "BIGINT", "int": "INT", "double": "DOUBLE",
        "decimal": "DECIMAL({precision},{scale})", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP",
    },
}

# 时间语义差异必须说出来，不能靠人记得
TIMESTAMP_NOTES: Dict[str, str] = {
    "mysql": "DATETIME 无时区：跨时区写入前先确认会话时区",
    "doris": "DATETIME 无时区，且不支持带时区的 TIMESTAMPTZ",
    "starrocks": "DATETIME 无时区",
    "odps": "DATETIME 无时区",
    "clickhouse": "DateTime64(3) 的时区随列定义（可写 DateTime64(3, 'Asia/Shanghai')），默认跟随服务端时区",
    "flink": "TIMESTAMP(3) 是本地时间语义；需要事件时间语义时用 TIMESTAMP_LTZ 并显式声明",
    "postgres": "TIMESTAMP 无时区；需要时区用 TIMESTAMPTZ（当前 spec 未区分，按需手工调整）",
}


class DdlError(ValueError):
    """spec 非法、类型不认识、方言不支持 —— 都要显式报错，不做近似映射。"""


def dialects_with_types() -> Tuple[str, ...]:
    return tuple(sorted(TYPE_MAP))


def physical_type(dialect: str, canonical: str, column: Dict[str, Any]) -> str:
    """canonical 类型 → 该方言的物理类型；未知类型/未知方言直接报错。"""
    canonical = str(canonical or "").strip().lower()
    if canonical not in CANONICAL_TYPES:
        raise DdlError(
            f"未知 canonical 类型 {canonical!r}。可用类型: {', '.join(CANONICAL_TYPES)}"
            "（要加类型请同时补 engine/core/ddl.py 的 CANONICAL_TYPES 与每个方言的映射）"
        )
    table = TYPE_MAP.get(str(dialect or "").strip().lower())
    if table is None:
        raise DdlError(
            f"未知方言 {dialect!r}，没有类型映射。可用方言: {', '.join(dialects_with_types())}"
        )
    template = table[canonical]
    precision = column.get("precision")
    scale = column.get("scale")
    if "{precision}" in template or "{scale}" in template:
        if precision is None or scale is None:
            raise DdlError(
                f"列 {column.get('name')!r} 的 decimal 类型需要 precision 与 scale"
                "（例如 {\"type\": \"decimal\", \"precision\": 18, \"scale\": 2}）"
            )
        template = template.replace("{precision}", str(int(precision))).replace("{scale}", str(int(scale)))
    return template


def _quote_string(value: Any) -> str:
    return "'" + str(value).replace("'", "\\'") + "'"


# 只有这些方言支持在列定义里内联 COMMENT；PostgreSQL 必须用 COMMENT ON COLUMN
INLINE_COMMENT_DIALECTS = (
    "mysql", "doris", "starrocks", "hive", "spark", "trino", "clickhouse", "flink", "odps",
)


def _column_line(dialect: str, column: Dict[str, Any]) -> str:
    name = str(column.get("name") or "").strip()
    if not name:
        raise DdlError(f"列缺 name：{column}")
    typed = physical_type(dialect, column.get("type"), column)
    line = f"  {name} {typed}"
    if column.get("nullable") is False:
        line += " NOT NULL"
    comment = str(column.get("comment") or "").strip()
    if comment and dialect in INLINE_COMMENT_DIALECTS:
        line += f" COMMENT {_quote_string(comment)}"
    return line


def _validate_spec(spec: Any) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise DdlError("spec 必须是 JSON 对象（含 table 与 columns）")
    table = str(spec.get("table") or "").strip()
    if not table:
        raise DdlError("spec 缺 table")
    columns = spec.get("columns")
    if not isinstance(columns, list) or not columns:
        raise DdlError("spec 缺 columns（非空数组）")
    return spec


def render_ddl(spec: Any, dialect: str, fmt_override: Optional[str] = None) -> Dict[str, Any]:
    """按方言（+ 可选湖表格式）渲染 CREATE TABLE。返回 {sql, dialect, format, table, warnings}。"""
    spec = _validate_spec(spec)
    dialect = str(dialect or "").strip().lower()
    if not TYPE_MAP.get(dialect):
        raise DdlError(
            f"未知方言 {dialect!r}，没有 DDL 模板。可用方言: {', '.join(dialects_with_types())}"
        )
    warnings: List[str] = []
    table = str(spec["table"]).strip()
    columns = spec["columns"]
    partition = [str(p) for p in (spec.get("partition_by") or [])]
    primary_key = [str(p) for p in (spec.get("primary_key") or [])]
    # 分区列在不同引擎里的声明位置不同（真实引擎教训）：
    # Hive/ODPS 的分区列**只**写在 PARTITIONED BY 里，若同时出现在列定义里就是
    # "Duplicate column name"；ClickHouse/PostgreSQL 的分区列必须是普通列。
    partition_set = {p.lower() for p in partition}
    effective_columns = list(columns)
    if dialect in ("hive", "odps") and partition_set:
        dropped = [c for c in columns if isinstance(c, dict) and str(c.get("name", "")).lower() in partition_set]
        if dropped:
            effective_columns = [
                c for c in columns
                if not (isinstance(c, dict) and str(c.get("name", "")).lower() in partition_set)
            ]
            warnings.append(
                f"{dialect} 的分区列只写在 PARTITIONED BY 里：已从列定义中移除 "
                f"{'、'.join(str(c.get('name')) for c in dropped)}（否则报 Duplicate column name）"
            )
    if partition and dialect in ("hive", "odps"):
        declared = {str(c.get("name", "")).lower() for c in columns if isinstance(c, dict)}
        for name in partition:
            if name.lower() not in declared:
                warnings.append(f"分区列 {name} 未在 columns 里给出类型，按 STRING 生成（Hive/ODPS 惯例）")
    if partition and dialect in ("clickhouse", "postgres", "spark", "trino"):
        declared = {str(c.get("name", "")).lower() for c in columns if isinstance(c, dict)}
        for name in partition:
            if name.lower() not in declared:
                warnings.append(
                    f"分区键 {name} 不在列定义里：{dialect} 的分区键引用的是已存在的列，请确认类型来源"
                )
    columns_sql = ",\n".join(
        _column_line(dialect, c if isinstance(c, dict) else {}) for c in effective_columns
    )
    comment = str(spec.get("comment") or "").strip()
    properties = spec.get("properties") or {}
    fmt = str(fmt_override or spec.get("format") or "none").strip().lower()
    if not isinstance(properties, dict):
        raise DdlError("spec.properties 必须是对象")
    # 组合未登记就报错：湖表格式与执行引擎是正交维度，不能靠"差不多能跑"猜
    try:
        engines.validate_format_for_dialect(dialect, fmt)
    except engines.UnknownEngine as err:
        raise DdlError(str(err))
    format_spec = formats.resolve(fmt)

    def props_clause(prefix: str, key_wrap: str = "'", sep: str = "=", data: Optional[Dict[str, Any]] = None) -> str:
        payload = properties if data is None else data
        if not payload:
            return ""
        rendered = ", ".join(
            f"{key_wrap}{k}{key_wrap}{sep}{_quote_string(v)}" for k, v in sorted(payload.items())
        )
        return f"{prefix} ({rendered})"

    comments_on: List[str] = []
    tail_statements: List[str] = []
    if dialect in ("hive", "odps"):
        sql = [f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n)"]
        if comment:
            sql.append(f"COMMENT {_quote_string(comment)}")
        if partition:
            sql.append(f"PARTITIONED BY ({', '.join(p + ' STRING' for p in partition)})")
        sql.append("STORED AS ALIORC" if dialect == "odps" else "STORED AS ORC")
        if properties:
            sql.append(props_clause("TBLPROPERTIES"))
    elif dialect == "spark":
        sql = [f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n)"]
        if comment:
            sql.append(f"COMMENT {_quote_string(comment)}")
        sql.append(f"USING {fmt if fmt != 'none' else 'parquet'}")
        if partition:
            sql.append(f"PARTITIONED BY ({', '.join(partition)})")
        if properties:
            sql.append(props_clause("TBLPROPERTIES"))
        if fmt == "none":
            warnings.append("spark 的 `USING parquet` 只是占位：表格式要在 spec.format 里显式选（iceberg/hudi/delta）")
    elif dialect == "trino":
        # Trino 不是 Spark 语法：格式与分区写在 WITH 里，不是 USING/PARTITIONED BY
        sql = [f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n)"]
        if comment:
            sql.append(f"COMMENT {_quote_string(comment)}")
        with_items = [(str(k), _quote_string(v)) for k, v in sorted(properties.items())]
        if partition:
            with_items.append(("partitioned_by", "ARRAY[" + ", ".join(_quote_string(p) for p in partition) + "]"))
            warnings.append(
                "Trino 的 partitioned_by 是 connector 属性（Hive/Iceberg 认它，memory/JDBC 不认）："
                "建表报 unknown property 就按目标 connector 的手册改"
            )
        if fmt != "none":
            warnings.append(
                f"Trino 的表格式由 catalog/connector 决定（connector={fmt}）："
                "DDL 里不生成任何 format 属性——它是 Hive connector 的文件格式键，不是通用属性"
            )
        if with_items:
            # 真实引擎教训：不要凭空补 format='PARQUET'，memory connector 直接报
            # "Catalog 'memory' table property 'format' does not exist"。
            sql.append("WITH (\n  " + ",\n  ".join(f"{k} = {v}" for k, v in with_items) + "\n)")
    elif dialect == "flink":
        head = f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}"
        if primary_key:
            head += f",\n  PRIMARY KEY ({', '.join(primary_key)}) NOT ENFORCED"
        sql = [head + "\n)"]
        if comment:
            sql.append(f"COMMENT {_quote_string(comment)}")
        if partition:
            sql.append(f"PARTITIONED BY ({', '.join(partition)})")
        merged = dict(properties)
        if fmt != "none":
            merged.setdefault("connector", fmt)
            warnings.append(
                f"{format_spec.label}：已补 'connector'='{fmt}'，其余参数（bucket/主键等）按 catalog 实际配置填"
            )
        if merged:
            sql.append(props_clause("WITH", data=merged))
        else:
            warnings.append("Flink 建表没有 connector 选项（spec.properties）：catalog 表可以，connector 表必须补")
    elif dialect == "clickhouse":
        engine_name = str(properties.get("engine") or "MergeTree")
        sql = [f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n)"]
        sql.append(f"ENGINE = {engine_name}")
        if partition:
            sql.append(f"PARTITION BY {', '.join(partition)}")
        order = primary_key or partition
        sql.append(f"ORDER BY ({', '.join(order)})" if order else "ORDER BY tuple()")
        if comment:
            sql.append(f"COMMENT {_quote_string(comment)}")
        if not order:
            warnings.append("ClickHouse 没给 primary_key/partition_by，ORDER BY tuple() 只是占位：排序键直接影响查询性能")
    elif dialect in ("mysql", "doris", "starrocks"):
        # MySQL 的主键在列定义里；Doris/StarRocks 的键子句在 `)` 之后（写在里面会语法错）
        if primary_key and dialect == "mysql":
            header = f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql},\n  PRIMARY KEY ({', '.join(primary_key)})\n)"
        else:
            header = f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n)"
        sql = [header]
        if dialect in ("doris", "starrocks"):
            # 真引擎实测（Doris 4.1.4.1）：ENGINE 必须在键子句**之前**。
            # 反过来写会直接语法错：mismatched input 'ENGINE' expecting {<EOF>, ';'}——
            # 解析器读完键子句就收尾了，所以顺序不是风格问题。
            sql.append("ENGINE = OLAP")
            if primary_key:
                sql.append(f"UNIQUE KEY ({', '.join(primary_key)})")
                warnings.append("Doris/StarRocks 的键子句按唯一模型生成（UNIQUE KEY）：聚合/明细模型请手工改成 AGGREGATE KEY / DUPLICATE KEY")
        else:
            sql.append("ENGINE = InnoDB")
        if comment:
            sql.append(f"COMMENT {_quote_string(comment)}")
        if dialect in ("doris", "starrocks"):
            key = primary_key or [c.get("name") for c in columns if isinstance(c, dict)][:1]
            distributed = f"DISTRIBUTED BY HASH({', '.join(str(k) for k in key)})"
            if dialect == "doris":
                sql.append(f"{distributed} BUCKETS AUTO")
                warnings.append("分布键/分桶数只是最小可用形态（BUCKETS AUTO）：按集群实际数据量确认")
            else:
                # 真引擎实测（StarRocks 3.3.9）：BUCKETS AUTO 是 Doris 的写法，StarRocks 会语法错
                # （Unexpected input 'AUTO'，最接近的输入是 INTEGER_VALUE）；省略 BUCKETS 时它自己分桶。
                sql.append(distributed)
                warnings.append("StarRocks 不写 BUCKETS：省略后由引擎自动分桶（3.3.9 实测可用）；要固定分桶数请手工补 BUCKETS n")
            if partition:
                warnings.append("Doris/StarRocks 的分区子句未自动生成（分区类型与取值范围要按业务定），spec.partition_by 仅供人工参考")
            if properties:
                sql.append(props_clause("PROPERTIES"))
            if not any(str(k) in ("replication_num", "replication_allocation") for k in (properties or {})):
                warnings.append(
                    "没给 replication_num/replication_allocation：Doris/StarRocks 默认副本数是 3，"
                    "单 BE 的本地集群建表会直接失败（replication num should be less than the number of "
                    "available backends）——单机联调时在 spec.properties 里加 "
                    "replication_allocation=tag.location.default:1"
                )
        elif properties:
            warnings.append("MySQL 没有表属性语法：spec.properties 已忽略（表模型/字符集请在建表语句里手工表达）")
    else:  # generic / postgres
        sql = [f"CREATE TABLE IF NOT EXISTS {table} (\n{columns_sql}\n)"]
        if partition:
            sql.append(f"PARTITION BY RANGE ({', '.join(partition)})")
            warnings.append(
                "PostgreSQL 声明式分区必须显式建分区，否则写入报 no partition of relation found："
                f"已附带一个 DEFAULT 分区（{table}_default），上线前请换成按时间范围的真实分区"
            )
        if dialect == "postgres":
            if partition:
                tail_statements.append(
                    f"CREATE TABLE IF NOT EXISTS {table}_default PARTITION OF {table} DEFAULT"
                )
            if properties:
                sql.append(props_clause("WITH", key_wrap=""))
                warnings.append("PostgreSQL 的 WITH (...) 是存储参数（fillfactor 之类），键名与取值按引擎确认")
            comments = []
            if comment:
                comments.append(f"COMMENT ON TABLE {table} IS {_quote_string(comment)};")
            for column in columns:
                if isinstance(column, dict) and str(column.get("comment") or "").strip():
                    comments.append(
                        f"COMMENT ON COLUMN {table}.{column['name']} IS {_quote_string(column['comment'])};"
                    )
            if comments:
                comments_on = comments
        elif properties:
            warnings.append(f"{dialect} 不做属性语法假设：spec.properties 未生成，请按目标引擎手工补")

    body = "\n".join(part for part in sql if part).rstrip()
    trailing = [*tail_statements, *comments_on]
    if trailing:
        # PostgreSQL 的分区与注释都是独立语句：CREATE 必须先收尾，否则会粘进建表语句里（语法错）
        if not body.endswith(";"):
            body += ";"
        body += "\n" + "\n".join(statement.rstrip(";") + ";" for statement in trailing)
    elif not body.endswith(";"):
        body += ";"
    note = TIMESTAMP_NOTES.get(dialect)
    if note and any(isinstance(c, dict) and str(c.get("type")).lower() == "timestamp" for c in columns):
        warnings.append(note)
    if fmt != "none":
        warnings.extend(formats.guidance(fmt))
    return {"sql": body, "dialect": dialect, "format": fmt, "table": table, "warnings": warnings}


def validate_ddl_types(sql: str, dialect: str, known: Optional[Tuple[str, ...]] = None) -> List[str]:
    """扫一段 CREATE TABLE，找出该方言里不认识的列类型。

    只做"类型名是否在该方言类型表里出现过"这一层判断，不解析完整语法——
    目的很明确：拦"照着别的引擎抄类型"这种错（Doris 的 DECIMALV3 抄到 Hive）。
    """
    dialect = str(dialect or "").strip().lower()
    table = TYPE_MAP.get(dialect)
    if table is None:
        raise DdlError(f"未知方言 {dialect!r}，无法校验 DDL 类型")
    # 该方言所有合法物理类型（去掉参数占位）
    allowed = set()
    for template in table.values():
        base = re.sub(r"\(.*\)", "", template).strip()
        allowed.add(base.lower())
    allowed |= {t.lower() for t in (known or ())}

    body = sql.split("(", 1)[1].rsplit(")", 1)[0] if "(" in sql and ")" in sql else ""
    unknown: List[str] = []
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.upper().startswith(("PRIMARY KEY", "UNIQUE KEY", "KEY ", "CONSTRAINT")):
            continue
        m = re.match(r"^[`\"\[]?([A-Za-z_][\w]*)[`\"\]]?\s+([A-Za-z][\w]*)", line)
        if not m:
            continue
        type_name = m.group(2).lower()
        if type_name not in allowed:
            unknown.append(f"{m.group(1)}: {m.group(2)}")
    return unknown
