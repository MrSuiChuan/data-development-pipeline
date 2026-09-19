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

## License

MIT — see LICENSE.
