# StarRocks：本轮真跑未完成（失败现场，状态=未验证）

**目的**：用当前代码在真 StarRocks 上复跑"建表 → 写入 → 读回 → 证据落账 → P3 出口门"（台账 LIVE-007）。

**结论：未验证。** 本机拿不到 StarRocks 的镜像（拉取多次尝试后仍未拉全），一次真跑都没跑起来。

**环境**：WSL2 Debian 13 / Docker 26.1.5（daemon 镜像源 `https://docker.m.daocloud.io`）
内存 7.8 GB（空闲约 6.7 GB）｜**日期**：2026-09-29
**纪律**：尝试前已停掉此前所有组件容器

## 失败现场（逐条留证据）

| 尝试 | 命令要点 | 原始结果 |
| --- | --- | --- |
| 官方 releases | `curl -I https://releases.starrocks.io/starrocks/StarRocks-3.3.9.tar.gz` | `403` |
| 清华镜像 | `curl -I https://mirrors.tuna.tsinghua.edu.cn/starrocks/` | `403` |
| 华为云镜像（目录） | `curl -I https://mirrors.huaweicloud.com/starrocks/` | `200`，但取具体文件时返回的是站点 SPA 页面（固定 12109 字节），不是 tar 包 |
| daocloud 镜像（显式前缀） | `docker pull m.daocloud.io/docker.io/starrocks/allin1-ubuntu:3.3.9` | 层层 retry，8 分钟超时（rc=124）；重试两次（总计约 45 分钟）后 `PULL_FAILED: 镜像仍未就绪` |
| 本机已有镜像 | `docker images | grep -i starrocks` | 无 |

## 这一项**没有**证明什么

- 没有证明 StarRocks 与当前代码不兼容（一次真跑都没跑起来）；
- 没有证明 `dialect.starrocks.*` 规则的判断是否正确——它仍是**未在真引擎上验证**的 WARN 规则；
- 矩阵里 StarRocks 记"未验证"，证据指向本文件。

## 跑通它需要什么（交接给下一个环境）

1. 一个**不拒绝 `starrocks/*` 命名空间**且带宽稳定的镜像源，或官方/信任镜像上的
   `StarRocks-<ver>.tar.gz`（FE+BE 同包）；
2. 起 FE（查询端口 9030、HTTP 8030）→ 在 FE 里 `ALTER SYSTEM ADD BACKEND "<be>:8040"` → `SHOW BACKENDS` 看到 Alive；
3. 内存预算：FE 约 1–2 GB、BE 约 2 GB；本机单独起是够的，前提是**其他组件全停**。

## 与插件无关的部分

DDP 侧配置按 MySQL 协议走（`kind=command` + `mysql` 客户端连 9030），
与 PostgreSQL/MySQL 两次真跑同一条路径；StarRocks 的建表模板（`UNIQUE KEY` + `DISTRIBUTED BY`）
与类型映射（`DECIMAL(18,2)` 等）有单测，但**没有**真引擎背书。

## 追加排查（2026-09-30，用户指出"可以装在 WSL"后重新逐条试过）

StarRocks 的镜像**确实存在**，本轮把"能不能拉到"与"能不能跑"分开了：

| 路径 | 结果 |
| --- | --- |
| 官方 Docker Hub 直连 | 超时（`registry-1.docker.io` 不可达） |
| `docker.1ms.run` / `docker.1panel.live` / `hub.rat.dev` 的 manifest | **OK**（三个镜像源都确认有 `starrocks/allin1-ubuntu:3.3.9`） |
| 实际拉取（每个镜像源都试了，单次上限 15 分钟、循环重试） | 层下载反复 `Retrying`：`docker.1ms.run` 超时（rc=124，但层有累积）、`docker.1panel.live` 连接超时、`hub.rat.dev` 卡在同一层 retry |
| 官方发行包 `releases.starrocks.io`（3.3.9 / 3.2.12，带浏览器 UA） | `403` |
| GitHub Releases（`api.github.com` 可达 ✓） | `StarRocks/starrocks` 的 release **资产数为 0**（3.5.21 / 4.0.14 / 3.5.20 均无资产） |
| 华为云镜像（目录与直连文件路径） | 一律返回站点 SPA 页面（固定 12109 字节），不是 tar 包 |

结论不变：**未验证**——镜像可达但层下载在本网络不稳定，多次尝试后仍未拉全；
换到带宽稳定的镜像源后按下面第 3 节照抄即可（起容器 → 9030 → `kind=command` + `mysql` 客户端）。

### 网络侧的最终形态（2026-09-30）

把"能不能装"与"这个网络能不能把包搬下来"彻底分开：

- **镜像存在**：`docker.1ms.run` / `docker.1panel.live` / `hub.rat.dev` 的 manifest 都返回 OK；
- **层在累积**：多轮 `docker pull`（每轮 15 分钟）里，每一轮都能把若干层下载完（日志里逐个 `Download complete`），
  但总有大层反复 `Retrying` 直到超时——所以这不是"没有镜像"，是**镜像源带宽/稳定性不足**；
- **可续跑**：层会留在本地，重跑 `docker pull` 从断点继续。
  仓库里留了可复现的循环脚本 `.tmp/live-starrocks/pull-loop.sh`（最多 8 轮，拉到就跑 run.sh）。

**本轮到这里停下的原因**：剩余轮次仍需数十分钟以上，且不保证成功；与其把时间花在等镜像，
不如把现场与续跑方式留清楚。**状态仍是未验证**，等镜像落地或换网络后按第 3 节照抄即可。
