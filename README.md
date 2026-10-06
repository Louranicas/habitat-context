# habitat-context

Tooling for the herdr habitat's Quick context protocol (ledger R-095). The protocol itself is in
`herdr.habitat.vault/00 Start/Quick context protocol.md`.

It runs on both machines and is host-aware:
- **Laptop** (Narrandera): built from the off-site snapshot.
- **Desktop** (Canberra): built from the live vaults.

## Commands

| Command | What it does |
|---|---|
| `habitat-ctx brief` | The SessionStart card (about 2 KB). It carries the protocol's short form, ledger counts, a **live health probe** (cached when the desktop is unreachable), blockers derived from that probe, and a STALE warning when the snapshot is more than 26 h old. |
| `habitat-ctx ask "<q>" [-k N] [--scope "70 Facets"] [--snippets] [--judge] [--jev-only]` | Hybrid retrieval: Turso FTS (BM25) plus section embeddings, fused by reciprocal rank, one result per note, with abstention at a vector distance of 0.53. It writes a receipt. |
| `habitat-ctx label "<q>" "<vault/path.md>"` | Records which note actually answered a real question. Feeds `--with-labels`. |
| `habitat-ctx refresh` | Off-site pull now, which chains the incremental rebuild (laptop). |
| `habitat-ctx stats` | What the DB holds, and receipts by origin. |
| `tests/eval_heldout.py [--fts-only] [--with-labels]` | Regression gate on the independent held-out set. Floors are ratcheted (currently hit@1 ≥ 17, hit@5 ≥ 33). |

**`--snippets`:** context packets of at most 8 excerpts and 24 KiB.
**`--judge`:** a local YES/NO re-rank of the top 5. Optional; its abstention is too aggressive (10/40 wrong).

## Measured (independent held-out set, n=40 + 8 off-topic, index v5)

| Measure | Result |
|---|---|
| Hybrid, @1 / @3 / @5 / @10 | 19 / 30 / 35 / 36 |
| Never retrieved | 4 |
| Off-topic abstained | 7/8 (the near-miss Terraform question is not) |
| FTS-only fallback, @1 / @5 | 15 / 30 |
| Latency | about 0.55 s per `ask` (persistent forward) |
| Startup brief | about 1.0 s |
| Read-set guarantee (conformal) | top 3 ≥ 70%, top 4 ≥ 80% |

## Files

| Path | Role |
|---|---|
| `build_context.py` | Incremental, generation-published build. Index v5 is: contextual section embeddings, plus note cards, plus doc2query rows (cached by sha in `d2q-cache.db`), with FTS on the body. |
| `bin/habitat-context-refresh` | Laptop: opens its own embedding tunnel. Desktop: uses local Ollama. |
| `bin/habitat-offsite-pull` | Laptop: off-site git mirrors and snapshots (R-060). |
| `systemd/` (laptop) | Pull timer, refresh drop-in, persistent embedding forward. |
| `systemd/desktop/` | Nightly refresh timer. |

## Rules learned the hard way (2026-10-06)

- **Single writer per DB, plus atomic publish.** Builds go into `.building`, are integrity-checked and checkpointed, renamed into place, and the old generation's WAL is removed.
- **pyturso 0.8.1 locks a DB file per process, even for reads.** Never hold a connection while calling `habitat-ctx`. `habitat-ctx` retries for about 3 s. `tursodb --readonly --mcp` does not block it.
- **`fts_score` needs a literal and a bare WHERE**, otherwise it is 0.0 for every row.
- **Ollama `/v1` silently truncates prompts at about 2k tokens.**
- **Test on copies** (`HABITAT_CTX_DB`, `HABITAT_CTX_RECEIPTS`). Never move a DB without its `-wal`. Never kill by a pattern that matches your own command line.
- **Measure on the independent set.** A self-written set scored 14/14 when the honest figure was 15/40.
- **Negative results:**
  - HyDE: @1 20 → 17.
  - A `(ctx, body)` FTS index: @5 30 → 21–25.
  - Tuning the FTS weight: within noise.
  - Full-corpus embeddings: unmeasurable on this set.
