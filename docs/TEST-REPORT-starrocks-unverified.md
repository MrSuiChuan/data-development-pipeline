# StarRocks：本轮真跑未完成（失败现场，状态=未验证）

> **已解决（2026-09-30）**：本文件记录的是 **2026-09-29** 的失败现场。第二天发现失败的真因不是
> "拉不下来"，而是**起法**——两个镜像的默认 `Cmd` 都是 `/bin/bash`，`docker run -d` 会立刻退出；
> 改成"容器保活 + `docker exec` 里 nohup 起进程"后链路真跑通过，并揪出修掉两处模板缺陷：
> 见 [TEST-REPORT-starrocks-live.md](TEST-REPORT-starrocks-live.md)。
> 下面的现场保留下来，是因为"镜像层跨轮续传"的排查记录与可续跑脚本仍然有效。

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

### 更好的配方：FE / BE 分开拉（2026-09-30 追加）

all-in-one 镜像（`starrocks/allin1-ubuntu:3.3.9`）太大、总在某一大层上反复重试；
官方 docker-compose 拓扑用的**两个小镜像**在三个镜像源上同样存在，实测更可行：

| 镜像 | hub.rat.dev | docker.1ms.run | docker.1panel.live |
| --- | --- | --- | --- |
| `starrocks/fe-ubuntu:3.3.9` | ✅ | ✅ | ✅ |
| `starrocks/be-ubuntu:3.3.9` | ✅ | ✅ | ✅ |

路线（脚本：`.tmp/live-starrocks/run-split.sh`）：

```bash
docker network create ddp-sr-net
docker run -d --name ddp-sr-fe --network ddp-sr-net --network-alias starrocks-fe \
  -p 8030:8030 -p 9020:9020 -p 9030:9030 hub.rat.dev/starrocks/fe-ubuntu:3.3.9
docker run -d --name ddp-sr-be --network ddp-sr-net --network-alias starrocks-be \
  -p 8040:8040 hub.rat.dev/starrocks/be-ubuntu:3.3.9
mysql -h 127.0.0.1 -P 9030 -uroot -e "ALTER SYSTEM ADD BACKEND 'starrocks-be:8040'"
# 等 SHOW BACKENDS 里 Alive=true，再跑 DDP 流程（kind=command + mysql 客户端，9030）
```

**当前进度（如实记录）**：FE 镜像的多层已陆续 `Download complete`（层会续传），
但每一轮 15 分钟仍会在某个大层上超时；脚本自带 4 轮重试，失败会打印 `PULL_FAILED`。
这一条仍是**未验证**——只把"怎么拉、拉到哪一步"写清楚。

**顺带踩到的一个坑**：想"单流下载会不会更稳"时写成了 `docker pull --max-concurrent-downloads=1 …`，
结果 12 轮全部 `rc=125`（usage error）——这个参数是 **dockerd 的配置项**（`/etc/docker/daemon.json`），
**不是 `docker pull` 的 flag**。差点把"我的命令写错"当成"镜像拉不动"记进报告，写在这里免得下一个人重踩。

### GitHub 路线（用户提示"下载不了就从 GitHub 下"后补测，2026-09-30）

| 试法 | 结果 |
| --- | --- |
| `ghcr.io/starrocks/allin1-ubuntu:3.3.9`、`ghcr.io/starrocks/{fe,be}-ubuntu:3.3.9` | MISS（ghcr 上没有） |
| `gh-proxy.com` 代理 `github.com/StarRocks/starrocks/releases/download/3.3.9/StarRocks-3.3.9.tar.gz` | **404**；同一代理取 `raw.githubusercontent.com` 是 200（代理可用） |
| GitHub Releases API | `StarRocks/starrocks` 各 release **资产数为 0**（3.5.21 / 4.0.14 / 3.5.20） |

即：**GitHub 上没有 StarRocks 的发行包**（官方只放在 releases.starrocks.io，本机 403）。
镜像仍是唯一可行来源——它确实存在（三个镜像源 manifest OK），瓶颈只是大层下载的稳定性。

### 把"开源 ≠ GitHub 上有二进制"钉死（用户追问后复测，2026-09-30 第二轮）

用户追问："doris 和 starrocks 不都是开源的组件吗，github 上拿不到吗"。分开核源码与产物（只读探测）：

| 试法 | 结果 |
| --- | --- |
| `api.github.com/repos/StarRocks/starrocks` | **200**：仓库在，`license.spdx_id = Apache-2.0`，源码约 841 MB，12k stars |
| `…/git/ref/tags/3.5.21` | **200**：tag 在 |
| Releases API（3.5.21 / 4.0.14 / 3.5.20 / 4.0.13 / 4.1.3） | 每个 release **`assets=0`**：没有二进制 |
| 官方二进制 CDN `releases.starrocks.io/starrocks/StarRocks-{3.5.21,3.3.9}.tar.gz` | **403**（根目录也是 403） |
| Apache CDN `dlcdn.apache.org/starrocks/…` 与 `downloads.apache.org/starrocks/…` | **404**（StarRocks 不在 Apache 名下） |
| 源码包（`main` 分支 tarball 走 `gh-proxy.com`） | **200**，25 秒收到 23.9 MB（流式，可拿） |
| 构建依赖 CDN `cdn-thirdparty.starrocks.com/…` | **206**（构建原料可达） |

结论：StarRocks 与 Doris 同型——**源码在 GitHub（可取）、发行包不在 GitHub**（官方 CDN 对本网络 403）。
它比 Doris 更窄：官方二进制 403 + Apache CDN 没有 + GitHub Releases 空 + ghcr 没有，
**镜像仍是唯一可用的二进制来源**，而镜像大层下载的稳定性是本轮的瓶颈（见上文）。
源码构建这条路的前提（`build.sh` / `build-in-docker.sh` / `thirdparty/vars*.sh`）都在仓库里，
但 `build-in-docker.sh` 依赖 Docker Hub（不可达），本地直构需要 C++ 工具链与 JDK——
与 Doris 一样属于"要用户拍板才动"的路径，本轮该目标（组件真跑矩阵）已按要求暂停。

### 第三轮复测：官方直链这条路也走不通（2026-09-30）

既然 Doris 那边已经用"从官方下载页取真实地址"的办法解了阻塞（见
`docs/TEST-REPORT-doris-unverified.md` 的「阻塞解除」小节），对 StarRocks 用同一招：

| 试法 | 结果 |
| --- | --- |
| 官方二进制 CDN `releases.starrocks.io`（`starrocks/StarRocks-3.5.21.tar.gz`、`…-3.3.9…`、`starrocks/3.5.21/…`、根路径，带浏览器 UA） | **全部 403**——不是路径错，是这一层对本网络直接拒答 |
| 官方下载页 `https://www.starrocks.io/download/community` | **200**，但页面是 JS 渲染（57 KB HTML 里没有任何 `.tar.gz` 或 releases 域名），抓不到直链 |
| 官方文档 `https://docs.starrocks.io/docs/deployment/deploy_manually/` | **200**，同样抓不到直链（95 KB HTML 里没有 `.tar.gz`） |
| Apache CDN（`dlcdn` / `downloads.apache.org/starrocks/`） | **404**（StarRocks 不在 Apache 名下） |

→ 与 Doris 的对照正好是一句话：**Doris 的官方二进制实测可达，StarRocks 的不可达**。
StarRocks 的阻塞仍然成立，且原因收窄为"官方 CDN 403 + 官网不给直链"；
镜像仍是唯一来源（三个镜像源 manifest 都能取到，瓶颈是大层下载的稳定性）。
