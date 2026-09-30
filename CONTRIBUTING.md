# Contributing

Thanks for considering a contribution to data-development-plugin.

## Development loop

```bash
# unit + e2e tests（数量以 discover 实际输出为准，别在这里写死）
python -m unittest discover -s tests

# compile check
python -m compileall -q engine hooks tests tools

# plugin manifest validation (Claude Code CLI)
claude plugin validate .
```

## Rules

- Do not edit `.data-dev/` runtime artifacts in commits; they are machine-owned.
- After changing anything under `engine/`, regenerate the integrity baseline:
  `python tools/generate_engine_manifest.py`. `tests/test_repo_invariants.py`
  会在清单过期时失败，并在消息里告诉你该跑哪条命令。
- After changing anything under `commands/`, regenerate the Codex surface:
  `python tools/build_codex_surface.py`. `tests/test_generated_surface.py`
  会逐字节比对，改了没重跑就失败。
- The base version lives in `.claude-plugin/plugin.json` only. `.codex-plugin`
  derives it (plus the `+codex.<token>` cachebuster from `tools/reinstall.py`),
  and `CHANGELOG.md` must have a matching latest released section —
  `tests/test_repo_invariants.py` checks all three agree.
- Keep the connector contract documented in `docs/REPRODUCTION-NOTES.md` in sync
  with `engine/core/clients.py` and `engine/core/sql_platforms.py`.
- Evidence keys (`verify_result`, `self_check_result`, `user_confirmed`, ...) are
  written only by `evidence` engine commands; never add new raw `ctx set` paths.
- Add regression tests for every gate or adapter change.
- Keep the Python floor honest: the minimum version comes from the `Python X.Y+`
  line in `README.md`, and `.github/workflows/ci.yml` must run the full suite on
  that version (`tests/test_repo_invariants.py` checks both, and also parses every
  source file with that version's grammar). New syntax or stdlib APIs must not
  raise the floor silently — if you need 3.10+, change the README line and the
  CI matrix together.

## License

MIT — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

**Inbound = outbound**：你提交的贡献以同一许可证（MIT）授权给本项目，
与 Apache 项目的惯例一致；提交即表示你同意这一点，并声明这些代码是你有权提交的。

**DCO 签署**：每个提交的说明里请带一行

```
Signed-off-by: Your Name <you@example.com>
```

（`git commit -s` 会自动加上）。私有环境下不强制，但**公开协作时没有这行的 PR 会被要求补签**。

## Community files

| 你想知道的 | 看这个文件 |
| --- | --- |
| 谁能合并、谁能发版、冲突怎么裁决 | [GOVERNANCE.md](GOVERNANCE.md) |
| 发布清单（版本单源 → 门禁 → tag） | [RELEASE.md](RELEASE.md) |
| 怎么相处、遇到骚扰找谁 | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| 提问前自查什么、问题该去哪 | [SUPPORT.md](SUPPORT.md) |
| 安全漏洞怎么报（**不要开公开 issue**） | [SECURITY.md](SECURITY.md) |

## Releasing

完整清单见 [RELEASE.md](RELEASE.md)；下面是本地几步的要点：

1. Bump the base version in `.claude-plugin/plugin.json` (the only place it is
   written by hand) and add a matching, non-`[Unreleased]` section at the top of
   `CHANGELOG.md`. `tests/test_repo_invariants.py` checks that the Claude
   manifest, the Codex manifest's base version and the newest released CHANGELOG
   section all agree.
2. Run `python tools/build_codex_surface.py` so the generated Codex manifest picks
   up the new base version (it preserves the `+codex.<token>` cachebuster suffix).
3. Run `python tools/reinstall.py` for a dry run, then `--apply` to bump the
   cachebuster and reinstall locally. **The host caches plugins by version**, so
   without the suffix change a reinstall copies the old files again.
4. Open the plugin in a **new thread** — the host only loads the new version in a
   fresh session.
