"""引擎能力注册表：本插件里"哪个引擎能做什么"的唯一权威。

三条规则（都不是设计洁癖，是踩出来的）：

1. **一个 kind 一条记录**：执行族（family）、方言（dialect）、必填配置键、默认端口、
   证据级别、可用湖表格式各写一次。执行器与检查器都从这里取，仓库里不允许有第二份清单。
2. **未登记的 kind 一律报错并列出可用项**。改动前 live 模式对未知 kind 静默落回
   `LivePlatformClient`（JSON stdout CLI 契约），用户把 kind 写成 clickhouse/hive 时
   不报错、不提示，只会拿到一个语义完全不同的客户端——证据语义就这样悄悄降级了。
3. **family 与 dialect 正交**：spark+iceberg 与 trino+iceberg 是"同一格式、不同方言"，
   两者都不等于"另一个连接器"；把它们塞进一个字段会让组合爆炸。

证据级别（``evidence``）只有三个取值，含义写死在代码里，报告与门控都要照它标注：

* ``server_query_id``：引擎/平台返回服务端 query id，可回查（CLI 契约族）；
* ``local_receipt``：本进程自造回执（``run_<hash>``）。**它不是平台 query id**，
  只能证明"这条 SQL 在本机执行过"，不能证明"平台侧查到过"；
* ``mock``：mock 模式的模拟回执，**不构成任何真实执行证据**；
* ``none``：不产生执行证据（会话内通道，预留给 kind=mcp）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 执行族
FAMILY_DBAPI = "dbapi"
FAMILY_COMMAND = "command"
FAMILY_CLI = "cli"
FAMILY_MCP = "mcp"

FAMILIES = (FAMILY_DBAPI, FAMILY_COMMAND, FAMILY_CLI, FAMILY_MCP)

# 证据级别
EVIDENCE_SERVER = "server_query_id"
EVIDENCE_LOCAL = "local_receipt"
EVIDENCE_MOCK = "mock"
EVIDENCE_NONE = "none"

EVIDENCE_LEVELS = (EVIDENCE_SERVER, EVIDENCE_LOCAL, EVIDENCE_MOCK, EVIDENCE_NONE)

# 湖表格式（与执行引擎正交）
FORMAT_NONE = "none"
FORMATS = (FORMAT_NONE, "iceberg", "hudi", "paimon", "delta")


class UnknownEngine(ValueError):
    """未登记的 kind：必须显式报错，不允许回退到别的适配器族。"""


@dataclass(frozen=True)
class EngineSpec:
    kind: str
    family: str
    dialect: str
    required: Tuple[str, ...] = ()
    default_port: Optional[int] = None
    evidence: str = EVIDENCE_LOCAL
    formats: Tuple[str, ...] = (FORMAT_NONE,)
    note: str = ""
    aliases: Tuple[str, ...] = field(default_factory=tuple)

    def missing_keys(self, platform: Dict[str, object]) -> List[str]:
        """按点号路径检查必填键（如 ``command.run``）。只报缺失，不做类型判断。"""
        missing: List[str] = []
        for key in self.required:
            node: object = platform
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node or node[part] in (None, "", [], {}):
                    missing.append(key)
                    break
                node = node[part]
        return missing


def _spec(**kwargs) -> EngineSpec:
    return EngineSpec(**kwargs)


# ---------------------------------------------------------------------------
# 注册表本体：加引擎只改这里，执行器与检查器不许再各自维护一份
# ---------------------------------------------------------------------------
ENGINES: Dict[str, EngineSpec] = {
    # --- dbapi：MySQL 协议族，pymysql 直连 ---
    "mysql": _spec(kind="mysql", family=FAMILY_DBAPI, dialect="mysql",
                   required=("host",), default_port=3306,
                   note="MySQL 协议直连（pymysql）"),
    "tidb": _spec(kind="tidb", family=FAMILY_DBAPI, dialect="mysql",
                  required=("host",), default_port=4000,
                  note="TiDB 走 MySQL 协议；方言按 MySQL 处理，TiDB 专有语法未单独建模"),
    "oceanbase": _spec(kind="oceanbase", family=FAMILY_DBAPI, dialect="mysql",
                       required=("host",), default_port=2881,
                       note="OceanBase MySQL 模式；Oracle 模式不在覆盖范围"),
    "doris": _spec(kind="doris", family=FAMILY_DBAPI, dialect="doris",
                   required=("host",), default_port=9030),
    "starrocks": _spec(kind="starrocks", family=FAMILY_DBAPI, dialect="starrocks",
                       required=("host",), default_port=9030,
                       note="与 Doris 方言相近但不等价（版本特性差异未逐条验证）"),
    "generic-sql": _spec(kind="generic-sql", family=FAMILY_DBAPI, dialect="generic",
                         required=("host",),
                         note="任何 MySQL 协议兼容库；方言规则只有基线 17 项"),

    # --- command：把 SQL 文件交给命令行客户端 ---
    "hive": _spec(kind="hive", family=FAMILY_COMMAND, dialect="hive",
                  required=("command.run",),
                  note="典型 argv: beeline -f {file}；非 ACID 表不支持 UPDATE/DELETE"),
    "spark": _spec(kind="spark", family=FAMILY_COMMAND, dialect="spark",
                   required=("command.run",),
                   formats=(FORMAT_NONE, "iceberg", "hudi", "delta", "paimon"),
                   note="典型 argv: spark-sql -f {file}；湖表能力取决于 catalog 配置"),
    "flink": _spec(kind="flink", family=FAMILY_COMMAND, dialect="flink",
                   required=("command.run",),
                   formats=(FORMAT_NONE, "paimon", "iceberg", "hudi"),
                   note="典型 argv: flink sql -f {file}；批模式需在脚本里显式设置 runtime-mode"),
    "paimon": _spec(kind="paimon", family=FAMILY_COMMAND, dialect="flink",
                    required=("command.run",), formats=("paimon",),
                    note="Paimon 通过 Flink/Spark catalog 访问，本身不是连接器族"),
    "clickhouse": _spec(kind="clickhouse", family=FAMILY_COMMAND, dialect="clickhouse",
                        required=("command.run",), default_port=9000,
                        note="典型 argv: clickhouse-client --multiquery --queries-file {file}；"
                             "MySQL 协议端口（9004）可走 kind=mysql 但语义受限"),
    "trino": _spec(kind="trino", family=FAMILY_COMMAND, dialect="trino",
                   required=("command.run",), default_port=8080,
                   formats=(FORMAT_NONE, "iceberg", "hudi", "delta"),
                   note="典型 argv: trino --file {file}"),
    "presto": _spec(kind="presto", family=FAMILY_COMMAND, dialect="trino",
                    required=("command.run",), default_port=8080,
                    note="Presto 与 Trino 同源，方言规则共用；版本差异未逐条验证"),
    "postgres": _spec(kind="postgres", family=FAMILY_COMMAND, dialect="postgres",
                      required=("command.run",), default_port=5432,
                      note="典型 argv: psql -f {file}；Greenplum 用 kind=greenplum"),
    "greenplum": _spec(kind="greenplum", family=FAMILY_COMMAND, dialect="postgres",
                       required=("command.run",), default_port=5432,
                       note="与 PostgreSQL 同方言；分布键/存储参数等 GP 专有项未建模"),
    "odps": _spec(kind="odps", family=FAMILY_COMMAND, dialect="odps",
                  required=("command.run",),
                  aliases=("maxcompute",),
                  note="典型 argv: odpscmd -f {file}；MaxCompute 不支持行级 UPDATE/DELETE"),
    "command": _spec(kind="command", family=FAMILY_COMMAND, dialect="generic",
                     required=("command.run",),
                     note="自定义 argv 模板；方言按 generic（只有基线 17 项）"),

    # --- cli：JSON stdout 契约的外部平台 CLI ---
    "cli": _spec(kind="cli", family=FAMILY_CLI, dialect="generic",
                 evidence=EVIDENCE_SERVER,
                 note="平台 CLI 契约（JSON stdout）；真实 query_id 由平台返回"),

    # --- mcp：会话内通道，引擎不直调 ---
    "mcp": _spec(kind="mcp", family=FAMILY_MCP, dialect="generic",
                 evidence=EVIDENCE_NONE,
                 note="预留给会话内模型调用，引擎不直调（见 docs/REPRODUCTION-NOTES.md 1.2.2）"),
}

_ALIASES: Dict[str, str] = {alias: spec.kind for spec in ENGINES.values() for alias in spec.aliases}


def canonical_kind(kind: str) -> str:
    """把别名归一到登记名（如 maxcompute → odps）；不做模糊匹配。"""
    lowered = str(kind or "").strip().lower()
    if lowered in ENGINES:
        return lowered
    if lowered in _ALIASES:
        return _ALIASES[lowered]
    return lowered


def available_kinds(family: Optional[str] = None) -> Tuple[str, ...]:
    return tuple(sorted(k for k, spec in ENGINES.items() if family is None or spec.family == family))


def resolve(kind: str) -> EngineSpec:
    """取一条登记；未登记时抛 UnknownEngine，并把可用 kind 一并给出。"""
    canonical = canonical_kind(kind)
    if canonical in ENGINES:
        return ENGINES[canonical]
    raise UnknownEngine(
        f"未登记的 platform.kind: {kind!r}。可用 kind: {', '.join(available_kinds())}"
        "（要接新引擎，请在 engine/core/engines.py 登记它的 family/dialect/必填键，"
        "不要让执行器猜）"
    )


def kinds_for_family(family: str) -> Tuple[str, ...]:
    return available_kinds(family)


def dialect_of(kind: str) -> str:
    return resolve(kind).dialect


def evidence_of(kind: str) -> str:
    return resolve(kind).evidence


def formats_of(kind: str) -> Tuple[str, ...]:
    return resolve(kind).formats


def formats_for_dialect(dialect: str) -> Tuple[str, ...]:
    """该方言下可用的湖表格式（多个 kind 共用方言时取并集）。"""
    target = str(dialect or "").strip().lower()
    out = set()
    for spec in ENGINES.values():
        if spec.dialect == target:
            out.update(spec.formats)
    return tuple(sorted(out)) or (FORMAT_NONE,)


def validate_format(kind: str, fmt: str) -> None:
    """校验 kind × format 组合；未登记的组合直接报错（不猜）。"""
    from . import formats as formats_module  # 局部导入：formats 不反向依赖 engines

    spec = resolve(kind)
    name = str(fmt or FORMAT_NONE).strip().lower() or FORMAT_NONE
    try:
        formats_module.resolve(name)  # 未知格式在这里报错
    except ValueError as err:
        raise UnknownEngine(str(err))
    if name not in spec.formats:
        raise UnknownEngine(
            f"{spec.kind} 未登记表格式 {name!r}。可用: {', '.join(spec.formats)}"
        )


def validate_format_for_dialect(dialect: str, fmt: str) -> None:
    """按方言校验 format（ddl 渲染没有 kind，只有 dialect）。"""
    from . import formats as formats_module

    name = str(fmt or FORMAT_NONE).strip().lower() or FORMAT_NONE
    try:
        formats_module.resolve(name)
    except ValueError as err:
        raise UnknownEngine(str(err))
    allowed = formats_for_dialect(dialect)
    if name not in allowed:
        raise UnknownEngine(
            f"方言 {dialect} 未登记表格式 {name!r}。可用: {', '.join(allowed)}"
        )


def validate_registry() -> List[str]:
    """注册表自检：结构错误必须在测试里就炸，而不是等用户踩。"""
    problems: List[str] = []
    seen_aliases: Dict[str, str] = {}
    for kind, spec in ENGINES.items():
        if kind != spec.kind:
            problems.append(f"{kind}: 键名与 spec.kind({spec.kind}) 不一致")
        if spec.family not in FAMILIES:
            problems.append(f"{kind}: 未知 family {spec.family!r}")
        if spec.evidence not in EVIDENCE_LEVELS:
            problems.append(f"{kind}: 未知 evidence {spec.evidence!r}")
        if not spec.dialect:
            problems.append(f"{kind}: 缺 dialect")
        for fmt in spec.formats:
            if fmt not in FORMATS:
                problems.append(f"{kind}: 未知 format {fmt!r}")
        for alias in spec.aliases:
            if alias in ENGINES:
                problems.append(f"{kind}: 别名 {alias!r} 与已登记 kind 冲突")
            if alias in seen_aliases:
                problems.append(f"{kind}: 别名 {alias!r} 与 {seen_aliases[alias]} 重复")
            seen_aliases[alias] = kind
    return problems


def dialect_from_config(config: Dict[str, object], default: str = "generic") -> str:
    """按项目配置推导方言；未登记 kind 会抛 UnknownEngine（由调用方决定怎么报）。

    platform 段缺失或 kind=cli 时返回 ``default``（generic）：CLI 契约族的方言由平台自己管，
    插件侧只做基线 17 项——这正是改动前的行为。

    ``kind=command`` 是通用 argv 执行器，它的 SQL 方言写在 ``platform.command.engine``
    （例如 ``{"kind": "command", "command": {"engine": "mysql", ...}}``）——真实引擎联调时
    发现只看 kind 会把 MySQL/Hive 的 DDL 与检查全部退化成 generic（见 docs/TEST-REPORT-mysql-live.md）。
    """
    platform = (config or {}).get("platform") or {}
    kind = str(platform.get("kind") or "cli").strip() or "cli"
    if kind == "cli":
        return default
    if kind == "command":
        engine = str((platform.get("command") or {}).get("engine") or "").strip().lower()
        if engine in ENGINES:
            return ENGINES[engine].dialect
        if engine:
            raise UnknownEngine(
                f"platform.command.engine 未登记: {engine!r}。可用: {', '.join(available_kinds())}"
                "（写具体引擎名才能拿到它的方言与检查；不写就按 generic）"
            )
        return default
    return resolve(kind).dialect


def _effective_dialect(platform: Dict[str, object], spec: EngineSpec) -> str:
    """kind=command 时返回 command.engine 的方言，与 dialect_from_config 保持同一口径。"""
    if spec.kind == "command":
        engine = str((platform.get("command") or {}).get("engine") or "").strip().lower()
        if engine in ENGINES:
            return ENGINES[engine].dialect
    return spec.dialect


def diagnose_platform(platform: Dict[str, object]) -> Dict[str, object]:
    """platform 段体检（纯函数、不碰网络）：缺键 / 未登记 kind / 明文口令 / profiles 形状。

    这是"配置是否配全"的判定，不是"连得上"的判定——连通性只有真跑才知道，
    所以这里绝不输出"连接成功"这类结论。
    """
    plat = dict(platform or {})
    kind = str(plat.get("kind") or "cli")
    report: Dict[str, object] = {"ok": True, "kind": kind, "errors": [], "warnings": []}
    errors: List[str] = report["errors"]  # type: ignore[assignment]
    warnings: List[str] = report["warnings"]  # type: ignore[assignment]
    try:
        spec = resolve(kind)
    except UnknownEngine as err:
        report["ok"] = False
        errors.append(str(err))
        return report

    report.update(
        {
            "canonical_kind": spec.kind,
            "family": spec.family,
            # kind=command 时真实方言来自 platform.command.engine（doctor 要跟渲染/检查口径一致）
            "dialect": _effective_dialect(plat, spec),
            "evidence": spec.evidence,
            "formats": list(spec.formats),
            "default_port": spec.default_port,
        }
    )
    missing = spec.missing_keys(plat)
    if missing:
        report["ok"] = False
        errors.append("缺必填配置键：platform." + "、platform.".join(missing))
    if str(plat.get("password") or "").strip():
        warnings.append("platform.password 是明文：改用 platform.password_env + 环境变量（密钥不进 config.json）")
    profiles = plat.get("profiles") or {}
    if not isinstance(profiles, dict):
        report["ok"] = False
        errors.append("platform.profiles 必须是对象（环境名 → 覆盖参数）")
    else:
        for name, profile in sorted(profiles.items()):
            if not isinstance(profile, dict):
                report["ok"] = False
                errors.append(f"platform.profiles.{name} 不是对象")
        if profiles:
            report["profiles"] = sorted(str(name) for name in profiles)
            base_env = str(plat.get("base_env") or "dev")
            for name in sorted(str(n) for n in profiles):
                if name == base_env:
                    warnings.append(f"profiles 里重复声明了 base_env={base_env}：两处不一致时会以 profiles 为准")
    if spec.evidence == EVIDENCE_LOCAL:
        warnings.append(
            "该执行族拿不到服务端 query_id：证据级别是 local_receipt，"
            "只能证明『这条 SQL 在本机执行过』，不能等价于平台 query_id"
        )
    fmt = str(plat.get("format") or "none").strip().lower()
    if fmt and fmt != FORMAT_NONE:
        try:
            validate_format(spec.kind, fmt)
            report["format"] = fmt
        except UnknownEngine as err:
            report["ok"] = False
            errors.append(str(err))
    if spec.note:
        report["note"] = spec.note
    return report
