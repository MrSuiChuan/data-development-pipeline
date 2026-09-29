# Apache Doris：本轮真跑未完成（失败现场，状态=未验证）

**目的**：用当前代码在真 Doris 上复跑"建表 → 写入 → 读回 → 证据落账 → P3 出口门"（台账 LIVE-006）。

**结论：未验证。** 本机拿不到 Doris 的发行包或镜像，一次真跑都没跑起来。

> 与仓库里既有的 `docs/TEST-REPORT-doris-live.md` 的关系：那份是 **2026-09-13**
> 由更早环境、更早代码版本留下的记录，**本轮没有复现**。本文件不复用、不覆盖它的结论，
> 只记录本轮为什么跑不动。矩阵里 Doris 的状态是"未验证"，证据指向本文件。

**环境**：WSL2 Debian 13 / Docker 26.1.5（daemon 镜像源 `https://docker.m.daocloud.io`）
内存 7.8 GB（空闲约 6.7 GB）、磁盘 899 GB 可用｜**日期**：2026-09-29
**纪律**：尝试前已停掉此前所有组件容器（一次只起必要的组件）

## 失败现场（四条路，逐条留原始错误）

| 尝试 | 命令要点 | 原始结果 |
| --- | --- | --- |
| Docker Hub 直连 | `docker pull apache/doris:2.1.7-fe-x86_64` | `Get "https://registry-1.docker.io/v2/": net/http: request canceled while waiting for connection (Client.Timeout exceeded while awaiting headers)` |
| daemon 已配的镜像源 | `docker pull apache/doris:2.1.7-fe-x86_64` | `denied`（镜像源拒绝该命名空间） |
| 显式镜像前缀 | `docker pull docker.m.daocloud.io/apache/doris:2.1.7-fe-x86_64` | `denied` |
| Apache 二进制包（Aliyun 镜像） | `curl -r 0-100 …/doris/3.1.4/apache-doris-3.1.4-bin-x64.tar.xz`（也试过 2.1.11 同形态） | `404`；目录只暴露 `2.0/ 2.1/ 3.0/ 3.1/ 4.0/` 层级，翻不到可下载文件名 |

补充事实：`docker manifest inspect apache/doris:2.1.7-fe-x86_64` 等全部 MISS——
它**不走** daemon 的镜像源，只能说明"Hub 直连不可用"，不能当成"镜像不存在"。

## 这一项**没有**证明什么（避免被读成结论）

- 没有证明 Doris 与当前代码不兼容（本轮没跑起来过）；
- 没有证明旧报告里的结论现在仍成立（那是更早环境与更早代码）；
- 因此矩阵里 Doris 记"未验证"，并把这份文件作为现场；旧报告保留为历史记录。

## 跑通它需要什么（交接给下一个环境）

1. 能访问 Docker Hub，或有**不拒绝 `apache/doris` 命名空间**的镜像源；或
2. 拿到 `apache-doris-<ver>-bin-x64.tar.xz`（FE+BE 同包）：起 FE（查询端口 9030）→
   FE 里 `ALTER SYSTEM ADD BACKEND` → 起 BE → `SHOW BACKENDS` 看到 Alive；
3. 内存预算：FE 约 1.5–2 GB、BE 约 2 GB。本机单独起是够的（空闲 6.7 GB），
   前提是**其他组件全停**——这正是本轮"一次只起一个"的纪律。

## 与插件无关的部分（顺带确认）

本轮为 Doris 准备的 DDP 侧配置是 command 族 + `mysql` 客户端（9030 走 MySQL 协议），
与 PostgreSQL/MySQL 两次真跑用的是同一条路径；方言规则 `dialect.doris.row_write_needs_key_model`
仍是**未在真引擎上验证**的状态（WARN）。
