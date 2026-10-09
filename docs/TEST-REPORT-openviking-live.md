# 真实 OpenViking 接入测试报告：数开插件 × 记忆插件 × data-warehouse-learning

**环境**

| 项 | 值 |
| --- | --- |
| OpenViking | `http://<OPENVIKING_HOST>:1933`（uvicorn，`GET /` → 302/405；服务在，不是空壳） |
| API Key | `~/.openviking/api.key`（110 B；**只以文件路径出现在配置里，内容不进任何产物**） |
| 语料 | `MrSuiChuan/data-warehouse-learning`（872 份 SQL，739 份含 `CREATE TABLE`） |
| 被测 | 离线数开插件 `data-development-plugin` v1.3.0+；记忆插件 `knowledge-base-plugin` 1.2.2 |
| 日期 | 2026-10-08 ｜ 台账 `goal#corpus-e2e-openviking`（`OV-001…OV-006`） |

> 远端**不是空的**：`domains/trade/**`（navigation/index/表文档 + `rules/trade/DQ-TRADE-PAID-ONLY.yaml`）
> 已经在里面，是本报告之前的真实内容。本轮只新增了 `domains/order/**`，没有覆盖 trade 域。

---

## 0. 结论速览

| 项 | 结果 |
| --- | --- |
| OV-001 两插件连通/鉴权自检 | ✅ 记忆插件 `ov-check` → `ok:true`；数开插件用真检索验通（更硬的证据在 OV-002） |
| OV-002 数开插件真召回 | ✅ 业务词与表名都命中真实远端内容；越界前缀、缺密钥两条负例均按预期被拒 |
| OV-003 记忆插件真实发布 | ✅ 开启 `ov.remote_enabled` 后 12 个对象推上 OpenViking，远端可检索到；**发现一个可观测性缺陷**（见第 3 节） |
| OV-004 端到端 | ✅ 语料 → 记忆插件生成 → OpenViking → 数开插件 `kb read` 精确读到正文 |
| OV-005 语料回归复跑 | ✅ 236/626 份文件数字与上一轮一致；三条已修规则仍为 0 命中 |
| 未覆盖 | 记忆插件 live 模式（缺真实 metadata MCP）、`ov-check` 不校验写权限、远端已有内容的清理（见第 6 节） |

---

## 1. OV-001 / OV-002：数开插件接真实 OpenViking

**配置**（密钥只写文件路径）：
```json
{"knowledge": {"kind": "http", "uri_prefix": "viking://resources/", "level": 2,
               "ov": {"url": "http://<OPENVIKING_HOST>:1933",
                      "api_key_file": "~\\.openviking\\api.key", "timeout": 60}}}
```

**真召回**（`ddp.py kb search --query …` → 服务端 `POST /api/v1/search/find`）：

```
query="订单"                → 命中 viking://resources/domains/trade/navigation.md（score 0.368）
                               与 viking://resources/rules/trade/DQ-TRADE-PAID-ONLY.yaml（0.366）
query="ods_order_detail_full" → 命中 domains/... 的 ODS 订单明细表文档（score 0.553），摘要与真实表结构一致
```

**负例**（都必须失败，不能静默降级）：

| 负例 | 实际 |
| --- | --- |
| `kb read --uri file:///etc/passwd` | ✅ `ClientError: 知识源越界: file:///etc/passwd（铁律: 必须以 viking://resources/ 开头）` |
| `kb read --uri viking://other/...` | ✅ 同样被前缀铁律拦下 |
| API Key 文件不存在 | ✅ `OpenViking find 失败[ov_config]: 读取 knowledge.ov.api_key_file 失败: /nonexistent/key.txt` |

---

## 2. OV-003：记忆插件把域知识发到真实 OpenViking

语料（真实 DDL 解析出的 8 张表）→ 记忆插件生成 → 发布：

```
ov-check               → {"ok": true, "configured": true, "server": "http://<OPENVIKING_HOST>:1933", "health": null}
publish（remote_enabled=false）→ {"ok": true, "version": "kb-v4", "commit": "b2016d7", "uris": [12 条], "consumer_sync": null}
publish（remote_enabled=true） → {"ok": true, "version": "kb-v5", "commit": "e33ddca", "uris": [12 条]}
```

**远端证据**（不是看返回值，是回头检索）：

```
ddp.py kb search --query "ods_activity_info_full"
  → viking://resources/domains/order/tables/ods_activity_info_full.md（score 0.535，摘要=该表结构说明）
  → viking://resources/domains/order/tables/ods_activity_rule_full.md（0.480）
```

---

## 3. 本轮发现的问题（1 个缺陷 + 2 个摩擦点）

| # | 问题 | 现场 | 影响/建议 |
| --- | --- | --- | --- |
| 1 | **`publish` 成功时不回报远端同步结果** | 第一次 publish（`ov.remote_enabled` 未开）与第二次（已开）返回结构**完全一样**：都是 `ok:true` + 版本号 + git commit + 12 条 URI，返回值里没有任何 remote 字段；只有远端**失败**时才返回 `gate: ov_remote_sync` | 在 `mock` 模式下"只提交了 git"和"真上了云"从返回值上无法区分——容易误判已发布。建议成功时也带 `remote: {pushed, verified, skipped}`（记忆插件仓库的问题，本报告如实记录，未改它的代码） |
| 2 | `ov-check` 只探 `GET /health` | 返回 `ok:true` 但 `health: null` | 自检通过 ≠ 写入权限可用；建议自检里加一次只读检索（或 `whoami` 类接口） |
| 3 | 远端内容需要"回头检索"才能证明 | 上面两条 | 建议把"发布后自动抽检一条远端对象"做成 publish 的内置步骤 |

---

## 4. OV-004：端到端（语料 → 记忆插件 → OpenViking → 数开插件）

```
语料 DDL（ods_activity_info_full 等 8 张表）
  → 记忆插件 snapshot/skeleton/generate（8 篇 9 章节文档 + navigation）
  → publish（git kb-v5 + OpenViking 12 个对象）
  → 数开插件 kb read --uri viking://resources/domains/order/tables/ods_activity_info_full.md
     → 返回正文（"# ods_activity_info_full / ## 1 基础身份 / - owner: None / - domain: order / - layer: ods …"）
```

链路每一跳都有产物可回查（URI、git commit `e33ddca`、远端检索命中）。同一份知识在两端的**标题与结构一致**，
没有出现"发布一份、检索到另一份"。

---

## 5. OV-005：语料回归复跑（确认没被本轮改动带跑偏）

| 方言 | 文件数 | 与上一轮对比 | 三条已修规则的命中数 |
| --- | --- | --- | --- |
| doris | 236 | 一致（no_table_annotation 236 / no_semicolon 145 / select_star 16 / name_mismatch 4 …） | unbalanced 0、target 0、partition_hint 0 |
| flink | 626 | 一致（no_table_annotation 625 / primary_key_needs_upsert 624 / name_mismatch 139 / select_star 57 …） | 同上，且新增 `env_script` WARN 1（替代原来错误的 target FAIL） |

结论：上一轮修的三条规则效果保持；DDL 真跑与六阶段 live 的结论沿用上一轮报告
（[TEST-REPORT-repo-corpus-e2e.md](TEST-REPORT-repo-corpus-e2e.md)），本轮改动只涉及知识通道。

---

## 6. 未覆盖与风险（如实列出）

1. **记忆插件 live 模式未跑**：`mode=live` 需要真实 metadata MCP；本轮仍用 mock 采语料元数据 + 真实 OpenViking 发布。
2. `ov-check` 不校验写权限（第 3 节 #2）；本轮是靠 publish 后的远端检索间接证明写入可用。
3. **远端写入不可一键回滚**：我在真实 OpenViking 上新增了 `viking://resources/domains/order/**`（12 个对象）。
   如需清除，请在 OpenViking 侧操作；`trade` 域的内容我未改动。
4. 语料的 DML（872 份中非 DDL 的部分）仍只做规则扫描，没有逐条真跑。
5. 端到端里的"工单系统"仍是 JSON CLI 替身（与 OpenViking 无关）。

---

## 7. 复跑命令

```bash
# 语料
git clone https://github.com/MrSuiChuan/data-warehouse-learning

# OV-001/002：配置 + 数开侧真召回
bash .tmp/ov/ov001.sh && bash .tmp/ov/ov002.sh

# OV-003：记忆插件真实发布（先开 ov.remote_enabled）
bash .tmp/ov/ov003.sh && bash .tmp/ov/ov003b.sh

# OV-004/005：端到端读取 + 语料回归复跑
bash .tmp/ov/ov004-005.sh
```

密钥：只放在 `~/.openviking/api.key`，配置里写 `api_key_file` 路径；
本报告与本轮所有产物都不含密钥内容。
