# MaxCompute / ODPS 接入

| 项 | 值 |
| --- | --- |
| kind | `odps`（别名 `maxcompute`） |
| family | `command` |
| dialect | `odps` |
| 必填配置键 | `platform.command.run` |
| 证据级别 | `local_receipt` |
| 验证状态 | 未在真实 MaxCompute 上联调 |

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
