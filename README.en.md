# data-development-plugin

A **dual-host** plugin (Claude Code **and** Codex) that turns offline data-task
development into a gated six-phase pipeline: request → design (SDS) →
architecture → development → testing → deployment → semantic/metadata sync.
Both hosts share one engine and one set of hooks.

Core idea: phases advance only on machine-checkable evidence, not on what the
model claims. PreToolUse hooks hard-block, PostToolUse validators bridge results
into state keys, and irreversible actions (prod publish/ALTER/backfill) always
ask the user.

## Highlights

- State machine with forward-only progress (back jumps need `--allow-back`).
- Evidence ledger: execution receipts are written by the engine and
  cross-checked by validators.
- Connector-based data sources: JSON-CLI, MySQL protocol (Doris/StarRocks/
  MySQL/TiDB...), command-run engines (Paimon/Flink/Spark/Trino...), and a
  reserved MCP channel.
- Environment profiles separate dev/staging/prod connections; backfills resume
  from a local ledger; rollback SQL is supported.

## Install (short version)

1. Install the plugin from a marketplace — see the Chinese [README.md](README.md)
   for both hosts (`/setup` + `/submit` … on Claude Code, `ddp-*` skills on Codex).
2. **On macOS/Linux, run `python3 tools/adapt_hooks.py` once.** The committed hook
   commands use the Windows launcher `py -3`; without adapting, every hook command
   fails, the host fails *open*, and all hard gates silently stop working.
   `python3 tools/adapt_hooks.py --check` reports whether this machine is OK.
3. In each data project, run `/setup` (Claude Code) or the `ddp-setup` skill (Codex).

## Docs

The Chinese docs are authoritative; this page is a summary.

- [README.md](README.md) — full setup, connectors, autopilot boundary
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — state contract, gate details
- [SECURITY.md](SECURITY.md) — security model and **known limits** (read this one)
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev loop and the checks CI enforces
- [CHANGELOG.md](CHANGELOG.md)

## License

Apache License 2.0 (`SPDX: Apache-2.0`) — full text in [LICENSE](LICENSE);
attribution and third-party boundaries in [NOTICE](NOTICE).
