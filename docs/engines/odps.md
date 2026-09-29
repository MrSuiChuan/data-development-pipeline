# MaxCompute / ODPS 接入

| 项 | 值 |
| --- | --- |
| kind | `odps`（别名 `maxcompute`） |
| family | `command` |
| dialect | `odps` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt` |
| 验证状态 | **不可本地复现**（云服务，本机无法起真实实例）；本地可验的部分见下 |

## 为什么"不可本地复现"，以及我们能验到哪一层

MaxCompute/ODPS 是**云服务**：要有账号、项目空间与 AK/STS 才能连，本机离线起不出一个真实实例。
这一条不写成含糊的"未验证"，而是分两层说清楚：

| 层次 | 能不能在本机验 | 现在有什么 |
| --- | --- | --- |
| 建表模板（类型映射 / `PARTITIONED BY` / `STORED AS` / TBLPROPERTIES 语法） | **能**（渲染层） | `ddl render --dialect odps` 有单测；分区列只进 `PARTITIONED BY` 的规则与 Hive 共用（Hive 4.0 已真跑通过） |
| 方言规则（行级写不支持、`INSERT OVERWRITE` 建议带分区） | **能**（规则层） | `dialect.odps.no_row_write` / `overwrite_without_partition` 有正反用例 |
| 配置体检（kind/必填键/profiles/明文口令/kind×format） | **能**（配置层） | `platform doctor` 有单测（`--dialect` 显示 odps） |
| 真跑建表/写入/读回/证据落账 | **不能** | 需要云账号 + 项目空间；`odpscmd` 的认证与退出码语义也未在本机验证 |

要真正跑通这一步，需要：一个可用的 MaxCompute 项目、`odpscmd`（含 AK/STS 配置或已登录的 profile）、
以及能访问 Endpoint 的网络。跑通后的验收标准与其它组件一致：
`platform doctor` → `ddl render` → `platform run-sql --save-evidence` → 读回行数一致 →
执行回执进账本 → `evidence verify` → P3 出口门 FAIL=0。

## 配置示例

```json
{
  "platform": {
    "kind": "odps",
    "command": {
      "engine": "odps",
      "run": ["odpscmd", "-f", "{file}"],
      "timeout": 1800
    }
  }
}
```

`odpscmd` 的 project 与 AK 走它自己的配置文件；插件只负责把 SQL 文件交出去并看退出码。

## 方言差异（`sql check --dialect odps`）

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `dialect.odps.no_row_write` | FAIL | MaxCompute 不支持行级 UPDATE/DELETE（事务表除外）；用 `INSERT OVERWRITE` 重写分区 |
| `dialect.odps.overwrite_without_partition` | WARN | 分区表 `INSERT OVERWRITE` 建议显式指定 `PARTITION` |

## 交付物惯例

- 分区表 DDL 用 `PARTITIONED BY (ds STRING)`，写入用动态分区 `PARTITION (ds)`；
- 无事务表场景不做行级更新；需要幂等就整分区覆盖；
- 提交与发布是平台侧概念（odpscmd 只执行 SQL），任务注册仍走平台的调度系统。

## 已知缺口

- 没验证 `odpscmd` 在无交互环境下的认证（AK/STS）与退出码语义；
- 分区剪裁、`LIFECYCLE`、`TBLPROPERTIES` 的建表模板未验证；
- 与阿里云 DataWorks 调度注解的对接只有 `-- schedule:` 一种约定（见 COMPAT-010）。
