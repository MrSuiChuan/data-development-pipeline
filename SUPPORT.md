# 求助（Support）

先说边界：这是一个**离线数据任务研发流程插件**（状态机 + 硬门控 + 配置化执行器），
不是数据平台本身。平台侧的问题（账号、集群、权限工单）请找对应平台的同事。

## 该去哪

| 你遇到的是 | 去哪 |
| --- | --- |
| 装不上、装完 hook 不生效 | 先看 README「安装」，再开 issue（附 `python tools/adapt_hooks.py --check` 的输出） |
| 流程/门控行为与文档不符 | 开 issue，附最小复现：工单结构、命令、完整输出 |
| 执行器（平台 CLI / MCP / Flink / Spark / …）连不上 | 先跑 `rtd-env`（`/env`）看缺口报告，再开 issue 附配置形状（**去掉口令**） |
| 想加一个执行器或数据源 | 看 `docs/REPRODUCTION-NOTES.md` 的连接器契约，直接提 PR |
| 安全漏洞 | **不要开公开 issue**，走 [SECURITY.md](SECURITY.md) |
| 参与方式与行为规范 | [CONTRIBUTING.md](CONTRIBUTING.md)、[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

## 提问前自查（30 秒）

```bash
python -m unittest discover -s tests    # 本机基线是否绿
python tools/adapt_hooks.py --check     # 非 Windows：hook 启动器是否可用（不可用=硬门静默失效）
rtd-status 或 py -3 .data-dev/engine/ddp.py status   # 当前阶段与缺口
```

这三条的输出贴进 issue，比描述十句"它不好使"有用。

## 我们不承诺什么

- 不承诺平台路径（平台 CLI / 各域 MCP）可用：那部分**未经真实环境验证**，如实写在
  `docs/validation-report.md` 与 README 的边界表里；
- 不承诺"AI 说什么就一定做了什么"：本插件的设计前提恰恰是不信自述、只认机器证据
  （见 `SECURITY.md` 的安全模型）；
- 不提供生产环境代运维。
