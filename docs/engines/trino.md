# Trino / Presto 接入

| 项 | 值 |
| --- | --- |
| kind | `trino`（`presto` 同方言） |
| family | `command` |
| dialect | `trino` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt` |
| 湖表格式 | `iceberg` / `hudi` / `delta` / `none` |
| 验证状态 | 未在真实 Trino/Presto 上联调 |

## 配置示例

```json
{
  "platform": {
    "kind": "trino",
    "command": {
      "engine": "trino",
      "run": ["trino", "--server", "http://trino:8080", "--catalog", "hive", "--file", "{file}"],
      "timeout": 900
    }
  }
}
```

`--catalog` / `--schema` 决定目标位置：**不写就会落到默认 catalog**，先按目标库显式指定。

## 方言差异（`sql check --dialect trino`）

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `dialect.trino.no_insert_overwrite` | FAIL | Trino/Presto 没有 `INSERT OVERWRITE`；用 `DELETE + INSERT` 或 connector 的 replace/CTAS |
| `dialect.trino.row_write_connector` | WARN | `UPDATE/DELETE` 依赖 connector（Hive connector 仅 ACID 表支持） |

## 交付物惯例

- 分区覆盖要拆成"删分区 + 写分区"两步，并在 `etl/` 里写清顺序（两步必须同一份真跑证据）；
- `ddl/` 里的表属性（`format`、`partitioning`）按 Iceberg/Hive connector 语法写；
- CTAS 适合重建场景，但会换表名/位置——用之前先确认下游依赖。

## 已知缺口

- 没验证 Trino 的 `DELETE`/`MERGE` 在各 connector 上的实际可用性；
- 没验证 Presto 与 Trino 在窗口函数/类型推断上的差异（共用方言规则，属已知近似）；
- 没验证 `--file` 模式下多语句执行与失败退出的行为。
