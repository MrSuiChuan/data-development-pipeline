# Security

## Reporting a vulnerability

**首选：GitHub 私密漏洞报告** —— 打开仓库的 **Security** 标签页 →
**Report a vulnerability**。这条路是私密的，只有维护者能看到，也支持贴最小复现。

如果那个入口不可用，就在仓库里开一个 issue，**只写「需要私下联系」**，
不要贴任何漏洞细节、复现步骤或数据样本；维护者会回你一个私下渠道。

> 维护者须知：上面那条入口要在 **Settings → Security → Private vulnerability
> reporting** 里开启，否则这段指引是死的。（原文写的是 "email in the marketplace
> metadata"，而 `.claude-plugin/plugin.json` 里根本没有邮箱——指向了一个不存在的东西。）

## Security model（三层防线的强度不一样，请分清）

| 层 | 机制 | 强度 |
| --- | --- | --- |
| 宿主 hook（PreToolUse） | 拦截工具调用：`dev_order_guard` / `difficulty_guard` / `runtime_guard` / `engine_readonly` / `state_write_guard` / `publish_gate` | **提高作弊成本**，不是沙箱 |
| 引擎内门控 | `engine/ddp.py` 每个动词先跑 `check_gate`；平台写动作（run-sql / create-task / publish / backfill）必须经引擎 | 与宿主无关，AI 绕不开 |
| 执行账本 | `.data-dev/runtime/executions.json` 与 `etl_dev_exec.json` / `test_runs.json` 交叉核验 `query_id` | **证据可信度的真正来源** |

一句话：hook 负责"让偷懒变贵"，账本 + 引擎门控负责"让撒谎不可能"。

## Known limits / non-goals（对抗式验收后如实记录）

1. **hook 的命令判定是启发式，不是 shell 解释器。** 它按分隔符切段、展开简单变量、
   识别 `cd` 进入机器资产目录等写法。下面这些原本被列为"可绕过"，**实测已经被拦住**，
   并且 `tests/test_known_limits.py` 会一直盯着它们不再回退：

   `& $c` 间接调用 · 环境变量拼路径 · 变量拼接出路径 · 从别的目录写回受保护位置 ·
   `python -c "... .write_text(...)"` · 先 `cd` 进受保护目录再写 · 管道写出 ·
   整条命令放进变量再 `iex`

   **仍然开着的口子只剩一个**：把写操作放进一个脚本文件、再执行那个脚本——hook 只看得见
   宿主暴露的那条命令，看不见脚本内容。**不要把它当作安全边界**，它是"提高成本 +
   留下痕迹"的防线。
2. **hook 只能看到宿主暴露的工具调用。** 子进程内部的副作用（脚本自己去连数据库、
   访问网络、写宿主看不到的位置）不在覆盖范围内；平台权限才是最后一道闸。
3. **Live 模式的证据强度取决于数据源适配器。** 经 pymysql 类适配器执行时拿到的是
   适配器生成的执行回执（`run_<hash>`），不是服务端 `query_id`；账本能证明"确实经引擎执行过"，
   但不能替代平台侧的查询审计。
4. **非目标：本插件不是沙箱、不是 RBAC、不校验连接器自身的信任模型。**
   生产使用建议：在容器/虚拟机里跑、给数据源配最小权限只读/限定库账号、
   对 `[已确认发布]` 这类人工门保持人参与。

## Design notes

- Machine evidence (`etl_dev_exec.json`, `test_runs.json`) is cross-checked
  against `.data-dev/runtime/executions.json`; treat that ledger as append-only
  (engine `runtime_guard` blocks tool writes/deletes).
- `.data-dev/engine/**` is a read-only engine copy (`engine_readonly`);
  `/setup` and `engine sync` are the only update paths.
- Never store database passwords in `config.json`; use `password_env`.
- Any open-source MCP/CLI bridge you connect inherits its own trust model; the
  plugin cannot vouch for a bridge's execution receipts.
