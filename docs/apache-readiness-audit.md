# 开源就绪审计（按 Apache 项目标准）

**这是一次判断，不是一句自我评价。** 每条给结论与依据；做不到的如实写"未做/需所有者操作"。
口径与另外两个插件（realtime-data-plugin、knowledge-base-plugin）相同，便于横向对照。

审计日期：2026-10-07 ｜ 版本基线：`.claude-plugin/plugin.json` 的 `1.3.0`

## 一、总览

| 维度 | 结论 |
| --- | --- |
| A 法律与许可 | **通过**：LICENSE 为 Apache-2.0 全文、`NOTICE` 写明许可/边界/第三方与运行时不入库、两份清单 license 与 NOTICE 署名一致（`tests/test_oss_readiness.py` 核对） |
| B 流程完整性 | **通过**：`CONTRIBUTING` / `GOVERNANCE` / `CODE_OF_CONDUCT` / `SUPPORT` / `SECURITY` / `RELEASE` 齐备；`.github/` 有 issue 与 PR 模板、CODEOWNERS |
| C 合理性 | **通过**：判据机器化（`tests/test_oss_readiness.py` 五项 + 反例）、发版清单可照抄、阶段门控与治理的关系写清 |
| D 友好性 | **通过**：README 有「30 秒上手」与「遇到 X 看哪个文件」；本轮补 `docs/glossary.md`（术语）与本报告（审计入口） |

本轮补齐（2026-10-07）：

- 新增 `docs/glossary.md`（16 个本仓库特定含义的词 + 4 条常见误解）；
- 新增本审计报告，并在 `README.md` 的文档索引里挂上入口；
- 新增 `.github/ISSUE_TEMPLATE/config-help.md`（配置求助模板，补齐 bug / feature 之外的第三类）；
- `tests/test_oss_readiness.py` 的必查清单纳入上述新增文件，防回归。

## 二、A 法律与许可

| # | 检查项 | 结论 | 依据 |
| --- | --- | --- | --- |
| A1 | LICENSE 是 OSI 认可的开源许可全文 | 通过 | `LICENSE` = Apache License 2.0 全文 |
| A2 | 项目名与版权声明 | 通过 | `NOTICE` 写明项目名、Copyright (c) 2026 AI实战技能圈、许可与保留义务 |
| A3 | 两份清单的 license 字段 | 通过 | 均为 `Apache-2.0`，与 `LICENSE` 一致（`test_oss_readiness.py` 按 SPDX 认正文而非首行） |
| A4 | 署名一致 | 通过 | 两份清单 `author.name` 与 `NOTICE` 一致；不留空 email |
| A5 | 第三方与分发边界 | 通过 | `NOTICE` 四条写清：运行时零第三方 Python 依赖、不分发大数据组件、仓库不含平台专有名、联调产物不入库 |
| A6 | 贡献授权链条 | 通过 | `CONTRIBUTING.md` 要求 DCO（`git commit -s`） |
| A7 | 自研源码许可头 | 通过 | `engine/` `hooks/` `tools/` `tests/` 逐文件带 Apache-2.0 头（由 `tools/validate_plugin.py` 与 CI 核对） |

## 三、B 流程完整性

| # | 检查项 | 结论 | 依据 |
| --- | --- | --- | --- |
| B1 | 谁能合并 / 谁能发版 | 通过 | `GOVERNANCE.md` + `.github/CODEOWNERS` |
| B2 | 决策与争议升级 | 通过 | `GOVERNANCE.md` |
| B3 | 行为准则 | 通过 | `CODE_OF_CONDUCT.md` |
| B4 | 安全报告路径 | 通过 | `SECURITY.md`（含已知边界与非目标） |
| B5 | 求助入口 | 通过 | `SUPPORT.md`：按问题给去向；明确"平台侧问题找平台"的边界 |
| B6 | 发版流程 | 通过 | `RELEASE.md`：版本单源 → 变更记录 → 生成物一致 → 门禁绿 → tag |
| B7 | 贡献入口 | 通过 | issue 模板三类（bug / feature / **配置求助**）+ PR 模板 + `config.yml` |

## 四、C 合理性

| # | 检查项 | 结论 | 依据 |
| --- | --- | --- | --- |
| C1 | 判据机器化且带反例 | 通过 | `tests/test_oss_readiness.py` 的五项（文件齐全 / 许可一致 / 运行时不入库 / 文档不写死测试数 / doc 索引可达），每条都有"故意破坏会红"的反例构造 |
| C2 | 文件之间不矛盾 | 通过 | 许可、署名、版本三处由测试交叉核对；文档不放测试数量（曾经 README 写 120 而实际 246，已被这条拦下） |
| C3 | 发版清单可执行 | 通过 | `RELEASE.md` 的每条命令都是仓库里真实存在的工具 |
| C4 | 治理与门控不打架 | 通过 | 治理决定"要不要改门"，不决定"这次能不能绕门"；绕过门的唯一合法路径是改代码 + 走治理 |

## 五、D 友好性

| # | 检查项 | 结论 | 依据 |
| --- | --- | --- | --- |
| D1 | 新人快速跑起来 | 通过 | README「30 秒上手（不碰任何真实系统）」 |
| D2 | 文档有索引 | 通过 | README「遇到 X 看哪个文件」；本轮新增的 glossary 与审计报告已挂进该表 |
| D3 | 术语有解释 | 通过 | `docs/glossary.md` + 常见误解 |
| D4 | 门控报错指向下一步 | 通过 | 阶段出口门返回检查键与可读原因（`docs/ARCHITECTURE.md` 第 3 节） |

## 六、需要所有者决策 / 未完成（如实留白）

1. **GitHub 侧设置只能在网页后台做**：开 **Private vulnerability reporting**、
   确认 `CODEOWNERS`（`@MrSuiChuan`）有效、按需开分支保护；仓库当前状态与公开时间由你决定。
2. **本机有一条未推送的提交**（`release: v1.3.0`）：本仓库现在的 `main` 比 `origin/main` 领先 1。
   推不推、何时推是你的动作。
3. **英文版治理文档未做**：`GOVERNANCE` / `CODE_OF_CONDUCT` / `SUPPORT` / `RELEASE` 目前只有中文，
   `README.en.md` 给出索引。
4. **运行时不入库这条依赖 git**：`tests/test_oss_readiness.py` 会调 `git ls-files`；
   在没有 git 的环境里该用例会跳过而不是误报（见该文件里的 `_git_ls_files`）。

## 七、怎么复核

```bash
python -m unittest tests.test_oss_readiness -v     # 五项机器判据 + 反例
python tools/validate_plugin.py .                  # 八项静态校验（含脱敏扫描与许可头）
python -m unittest discover -s tests               # 全量
```
