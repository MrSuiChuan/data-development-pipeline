---
name: 配置 / 接入求助
about: 接真实平台执行器、配连接器、或阶段门控报缺口时卡住了
title: "[config] "
labels: configuration
---

## 卡在哪一步

- [ ] 安装 / hook 不生效
- [ ] 初始化与配置（`.data-dev/config.json`）
- [ ] 连接器（平台 CLI / 各域 MCP）连不上
- [ ] 某阶段出口门一直不放行
- [ ] 回刷 / 部署的确认门
- [ ] 其他：

```bash
# 命令 + 完整输出（口令、内网地址请打码）
```

## 自检结果

```bash
python tools/adapt_hooks.py --check     # hook 启动器在本机是否可用
python -m ddp status                    # 或按 README 的对应命令打印当前阶段
```

## 配置形状（脱敏后）

<!-- 贴 .data-dev/config.json 的结构即可，口令写 <redacted>；不要贴平台专有名与内部代号。 -->

## 你查过哪些文档

<!-- 例如 README 的「遇到 X 看哪个文件」、docs/ARCHITECTURE.md 的哪一节。查过但没解决，说明文档该补。 -->
