---
name: Bug report
about: 报告一个可复现的问题
title: "[bug] "
labels: bug
---

## 先跑这两条，把结果贴上来

它们能把「本机没装好」和「插件真的坏了」分开——十有八九的问题在第一条。

```bash
python3 tools/adapt_hooks.py --check     # hook 启动器在本机是否可用
python3 -m unittest discover -s tests    # 本机全量测试是否通过
```

> `--check` 返回非零 = hooks 配置的启动器在本机跑不起来，宿主会把 hook 失败当成
> **放行**，所有硬门静默失效。这不是插件坏了，跑一次 `python3 tools/adapt_hooks.py`
> 即可；请把输出原样贴在下面。

## 环境

- 宿主与版本（Claude Code / Codex）：
- 插件版本（`.codex-plugin/plugin.json` 里的 `version`）：
- 操作系统：
- Python 版本（`python3 -V`）：
- mock 还是 live 模式：

## 复现

最小命令序列（能贴原始输出就贴）：

```bash

```

## 期望 vs 实际

期望：

实际：

## 与 hook 相关的话

设 `DDP_HOOK_TRACE=<某个文件>` 后重现一次，把落盘的事件贴上来（它记录两个宿主发给
hook 的原始 payload）。
