---
description: 建数据需求工单（8 项质量门），流水线入口
argument-hint: <需求一句话…>（也可直接描述需求内容）
allowed-tools: [Bash, Read, Write, AskUserQuestion]
---

# /submit — 建单（业务态 → 待 SDS）

把一段需求文字转成合格的数据任务工单。**唯一入口**：工单主题必须以
`【数据需求】` 开头，描述必须包含 5 章（背景 / 目标 / 范围 / 验收口径 / 风险）
及目标表、来源、验收、负责人、计划日期等要素。

## 步骤

1. 先确认 `/setup` 已完成且 `.data-dev/engine/ddp.py` 存在；否则提示先跑 /setup。

2. 用 `$ARGUMENTS`（或对用户追问后得到的信息）写成完整描述，覆盖 5 章与 8 项质量要素。
   把描述写入临时文件 `描述-<序号>.md` 以免命令行转义问题。

3. 建单（Windows 用 `py -3`，macOS/Linux 用 `python3`）：

   ```
   py -3 .data-dev/engine/ddp.py ticket create --title "【数据需求】<标题>" --description-file 描述-<序号>.md
   ```

   引擎执行 8 项质量门；失败会返回具体缺项，**必须补齐重试，不允许 --force 绕过**。

4. 建单成功得到 `T-xxxx`。输出给用户：

   - 工单号、模板类型（数据任务）；
   - 下一步：`/sds-design T-xxxx` 进入数据概要设计；
   - 提示：在设计(SDS)未终版贴单前，`/start` 会被准入门拦住。

5. 删除临时描述文件。

> live 模式：引擎会调用 `tracker create`；mock 模式数据落在 `.data-dev/mock/tracker/`。
