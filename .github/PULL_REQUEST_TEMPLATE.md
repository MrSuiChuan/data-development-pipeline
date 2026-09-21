## 改了什么

## 自查

CI 会跑，但先本地过一遍能省一轮来回：

- [ ] `python3 -m unittest discover -s tests` 全过
- [ ] 改了 `engine/` → 跑过 `python3 tools/generate_engine_manifest.py`
      （忘了重跑，贡献者 `/setup` 之后的 engine_integrity 自检会在**用户侧**报错）
- [ ] 改了 `commands/` → 跑过 `python3 tools/build_codex_surface.py`
      （`tests/test_generated_surface.py` 会逐字节比对，忘了会红）
- [ ] 改了门控、守卫或适配器 → 补了回归用例
- [ ] 没有提交 `.data-dev/` 运行时产物
- [ ] 版本号没动；要发版请单独说，基础版本只在 `.claude-plugin/plugin.json` 里改

## 影响面

- 是否改变任何门控语义（拦得住 / 拦不住的边界）：
- 是否需要同步改 `SECURITY.md` 或 `docs/ARCHITECTURE.md`：
