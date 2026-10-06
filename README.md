# habitat-context

Quick context protocol tooling for the herdr habitat (ledger R-095; protocol note
`herdr.habitat.vault/00 Start/Quick context protocol.md`). It runs on the Narrandera laptop and is
read-only toward the Canberra desktop.

| Path | Role |
|---|---|
| `habitat-ctx` | `brief` (SessionStart card), `ask` (Turso FTS + section embeddings, RRF, per-note, abstention, receipts), `stats` |
| `build_context.py` | Incremental build of `habitat-context.db` from the off-site vault snapshot (v3 fenced, `_sources/` excluded) |
| `bin/habitat-offsite-pull` | Daily off-site pull: git mirrors plus hard-linked snapshots, restore-checked (R-060) |
| `bin/habitat-context-refresh` | Opens its own embedding tunnel, runs the incremental build, closes the tunnel by PID |
| `systemd/` | User units, symlinked into `~/.config/systemd/user/`. The refresh runs as `ExecStartPost` of the pull |

Traps learnt (2026-10-06):
- `fts_score` needs a literal and a bare WHERE.
- Ollama `/v1` truncates at about 2k tokens.
- Never move a DB without its `-wal`.
- Never kill by a pattern that matches your own command line.
