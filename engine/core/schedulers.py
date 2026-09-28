"""调度目标：dp_task 的调度注解在不同调度系统下要求不同。

过去的第 8 条零容忍规则只问"有没有 `-- schedule:`"。多目标场景下这不够：
Airflow 缺时区就是 UTC 跑、DolphinScheduler 的工作流名与任务名不在一处、
DataWorks 要看上游依赖、纯执行引擎（MySQL/Doris/Paimon…）压根没有调度器——
在这几种情况下"有注解"和"注解正确"是两件事。

所以调度目标是**数据**（`SCHEDULERS`）：每个目标声明必填注解（缺了 FAIL）与建议注解（缺了 WARN）。
默认是 `cron`，行为与改动前完全一致（只要非空 `-- schedule:` 就算通过）。
`none` 表示"这个项目没有调度器"，此时不要求调度注解，但写了注解会 WARN 提醒"没人消费它"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SchedulerSpec:
    kind: str
    label: str
    requires: Tuple[str, ...] = ()          # 缺失即 FAIL 的注解键（schedule 恒为必填，除 kind=none）
    recommends: Tuple[str, ...] = ()        # 缺失即 WARN 的注解键
    note: str = ""


SCHEDULERS: Dict[str, SchedulerSpec] = {
    "cron": SchedulerSpec(
        kind="cron",
        label="通用 cron（默认）",
        note="只要求 -- schedule: 非空；cron 语义与执行时区由平台侧负责",
    ),
    "airflow": SchedulerSpec(
        kind="airflow",
        label="Apache Airflow",
        requires=("schedule-id",),
        recommends=("schedule-timezone",),
        note="Airflow 默认 UTC：不写 schedule-timezone 就要按 UTC 解释 cron（时区坑最常见）",
    ),
    "dolphinscheduler": SchedulerSpec(
        kind="dolphinscheduler",
        label="Apache DolphinScheduler",
        requires=("schedule-id",),
        recommends=("schedule-timezone",),
        note="工作流与任务同名约定要显式写：schedule-id 指向工作流定义名",
    ),
    "dataworks": SchedulerSpec(
        kind="dataworks",
        label="DataWorks 调度",
        recommends=("schedule-depends",),
        note="DataWorks 依赖上游产出：schedule-depends 列出上游任务或表，便于调度侧配依赖",
    ),
    "none": SchedulerSpec(
        kind="none",
        label="无调度器（纯执行引擎）",
        note="项目只有执行引擎（MySQL/Doris/Paimon…）：不做调度校验，写了注解会提醒没人消费",
    ),
}


def available() -> Tuple[str, ...]:
    return tuple(sorted(SCHEDULERS))


def resolve(kind: Optional[str]) -> SchedulerSpec:
    key = str(kind or "cron").strip().lower() or "cron"
    if key not in SCHEDULERS:
        raise ValueError(
            f"未登记的 scheduler.kind: {kind!r}。可用: {', '.join(available())}"
            "（要加目标请在 engine/core/schedulers.py 登记它的必填/建议注解）"
        )
    return SCHEDULERS[key]


def validate_registry() -> List[str]:
    problems: List[str] = []
    for key, spec in SCHEDULERS.items():
        if key != spec.kind:
            problems.append(f"{key}: 键名与 spec.kind({spec.kind}) 不一致")
        if "schedule" in spec.requires:
            problems.append(f"{key}: schedule 恒为必填，不要写进 requires（避免两处判）")
        for field_name in list(spec.requires) + list(spec.recommends):
            if not field_name or field_name.startswith("--"):
                problems.append(f"{key}: 注解键写法应为裸名（如 schedule-id），得到 {field_name!r}")
    return problems
