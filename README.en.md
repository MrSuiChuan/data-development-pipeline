# data-development-plugin

A Claude Code plugin that turns offline data-task development into a gated
six-phase pipeline: request → design (SDS) → architecture → development →
testing → deployment → semantic/metadata sync.

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

Chinese docs: [README.md](README.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md)
