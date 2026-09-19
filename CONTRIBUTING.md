# Contributing

Thanks for considering a contribution to data-development-plugin.

## Development loop

```bash
# unit + e2e tests (69)
python -m unittest discover -s tests

# compile check
python -m compileall -q engine hooks tests tools

# plugin manifest validation (Claude Code CLI)
claude plugin validate .
```

## Rules

- Do not edit `.data-dev/` runtime artifacts in commits; they are machine-owned.
- After changing anything under `engine/`, regenerate the integrity baseline:
  `python tools/generate_engine_manifest.py`.
- Keep the connector contract documented in `docs/REPRODUCTION-NOTES.md` in sync
  with `engine/core/clients.py` and `engine/core/sql_platforms.py`.
- Evidence keys (`verify_result`, `self_check_result`, `user_confirmed`, ...) are
  written only by `evidence` engine commands; never add new raw `ctx set` paths.
- Add regression tests for every gate or adapter change.

## License

MIT — see LICENSE.
