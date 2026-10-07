# 发布流程（Release）

发布只有一条路：**版本单源 → 变更记录 → 生成物一致 → 全部门禁绿 → 打 tag**。
清单里的每条命令都是仓库里真实存在的，照着跑即可。

## 0. 前提

- 本仓库**没有需要单独打包的二进制**：仓库本身就是插件目录（Claude Code / Codex 都能直接装）；
  面向内部 marketplace 时用 `tools/wrap_marketplace.py` 包装。
- 版本号只有一处权威：`.claude-plugin/plugin.json` 的 `version`。
  `.codex-plugin` 由生成器派生（带 `+codex.<token>` 缓存串），`CHANGELOG.md` 必须有同名小节。

## 1. 冻结候选

```bash
git switch -c release/vX.Y.Z        # 从 main 切发布分支
python -m unittest discover -s tests    # 全量用例：门控/状态机/不变量/开源就绪都在这套里（数量以输出为准）
python -m compileall -q engine hooks tests tools
# 生成物与命令源一致：跑生成器后比对（该工具没有 --check，它只会重写；
# 逐字节比对在 tests/test_generated_surface.py 里）
python tools/build_codex_surface.py && git diff --exit-code -- .codex-plugin hooks skills engine/templates
python -m unittest tests.test_generated_surface
python tools/adapt_hooks.py --check           # 非 Windows 平台必跑（启动器不可用会让 hook 静默失效）
git status --short                            # 必须为空：生成物已提交
```

## 2. 版本与变更记录

1. 改 `.claude-plugin/plugin.json` 的 `version`（唯一版本源）；
2. 在 `CHANGELOG.md` 顶部加同名小节：**写清"谁受影响、要不要迁移、怎么迁移"**，
   不要只写"修复若干问题"；
3. 跑一遍 `tools/build_codex_surface.py` 让 Codex 面（`plugin.json` / hooks / 入口技能）跟上；
4. `python tools/reinstall.py`（在宿主侧使用时需要缓存串，避免加载旧副本）。

## 3. 打 tag

```bash
git commit -am "release: vX.Y.Z"
git tag -a vX.Y.Z -m "data-development-plugin vX.Y.Z"
git push origin main --follow-tags
```

tag 说明里写：通过的门禁（上面的命令与结果）、已知边界（例如"某组件未在真实环境验证"）、
以及任何跳过的清单项与原因。

## 4. 发布后

- 在 issue/公告里给"从旧版升级"的三步说明（拉取 → 重跑 `tools/adapt_hooks.py`（非 Windows）→ 开新会话）；
- 若发布里包含"未验证"的能力，**公告里也要写"未验证"**——本仓库的规矩是
  未验证的东西不许在发布说明里被说成已支持（见 `docs/validation-report.md`）。

## 5. 撤回（万一）

发现严重问题时：先改回版本号并在 `CHANGELOG.md` 标注撤回原因，再打 `vX.Y.(Z+1)`；
`git tag` 不删除（历史可追溯），公告里指向新版本。
