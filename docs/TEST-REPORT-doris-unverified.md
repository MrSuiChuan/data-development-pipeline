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

## 追加排查（2026-09-30，用户指出"可以装在 WSL"后重新逐条试过）

用户提示"组件可以装在 WSL 里"是对的——**Doris 本身当然能装**（官方就是 FE+BE 两个目录的 tar 包），
卡点是**这个网络拿不到包**。为把结论钉死，又试了下面这些路径，全部失败：

| 路径 | 结果 |
| --- | --- |
| 官方 Docker Hub（`apache/doris:2.1.7-fe-x86_64` 等 **9 个 tag**） | 直连超时 / 镜像源 `denied` / 三个第三方镜像（hub.rat.dev、docker.1ms.run、docker.1panel.live、dockerpull.org）**全部 MISS** |
| Aliyun ACR（`registry.cn-hangzhou.aliyuncs.com/doris/doris`、`…/selectdb/doris`、北京区） | MISS |
| Apache 镜像（Aliyun / USTC / 清华）：只有 `-src.tar.gz` | 目录里 **只有源码包**，没有 `-bin-x64.tar.xz` |
| 官方二进制 OSS 桶（`apache-doris-releases.oss-{accelerate,cn-beijing}.aliyuncs.com`）：逐个版本试了 2.0.3 / 2.1.0 / 2.1.7 / 2.1.9 / 2.1.11 / 3.0.0 / 3.0.3 / 3.0.5 / 3.1.0 / 3.1.3 / 3.1.4 | 全部 `404`；桶**存在**（返回 OSS 错误 XML），但对象名猜不中 |
| OSS 桶列举（`?list-type=2`） | `AccessDenied: The bucket you access does not belong to you` |
| GitHub Releases（`api.github.com` 可达 ✓） | `apache/doris` 的 release **资产数为 0**（4.1.4.1 / 4.1.4 / 4.0.8 均无资产） |
| 高校镜像 `/doris`（清华 403、北外 403、上交 404） | 不可用 |

结论不变：**未验证**，但原因收敛成一句话——**Doris 的发行包在这个网络里拿不到**（不是装不了，
也不是插件不兼容）。下面第 3 节的命令序列在有网的环境里可以直接照抄。

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
