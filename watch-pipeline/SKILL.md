---
name: watch-pipeline
description: Maintain the collection layer — the watch_* scrapers behind .github/workflows/watch-pipeline.yml and their four channels (models-dev, opencode-go, GOAT, Arena). Adapt selectors when an upstream page changes, guided by per-channel field contracts in reference/<channel>/extra.json and the persisted last-error.json. Pipeline execution belongs to CI; agents do not run full scrapes against live upstreams.
---

# Watch Pipeline

This skill is the maintenance manual for the collection layer. It owns `watch-pipeline/scripts/` (the `watch_*.py` scrapers plus `error_state.py`), their tests, and the per-channel `reference/` directories. It does NOT own `models-mapping/` (transformation) and never writes AxonHub.

## Channels

| Channel | Script | Upstream | Snapshot | Reference |
|---|---|---|---|---|
| models-dev | *(pure `curl` in yml, no script)* | models.dev/models.json | `data/all_models.json` | — |
| opencode-go | `watch-pipeline/scripts/watch_go.py` | opencode `go.mdx` | `data/opencode-go-models.json` (enriched in place) | `reference/go/` |
| goat | `watch-pipeline/scripts/watch_goat.py` | commandcode.ai GOAT plan page | `data/goat-models.json` | `reference/goat/` |
| arena | `watch-pipeline/scripts/watch_arena.py` | lmarena.ai WebDev leaderboard | `data/arena.json` | `reference/arena/` |

Execution entrypoint is `.github/workflows/watch-pipeline.yml` (daily cron). Every script is stdlib-only Python 3.12+ and replays offline via `--html` / positional input / `--input` respectively.

## Field contracts

`reference/<channel>/extra.json` is the machine-readable definition of what each scraper extracts: which upstream column/field each snapshot field comes from, its type, parse rule, and where it lands in the snapshot. When adapting a script, read the channel's extra.json first — the fix must keep the snapshot matching that contract. The snapshot shapes themselves never change here; changing them is an ADR-level decision.

## Error repair workflow

Each script persists failures to `watch-pipeline/reference/<channel>/last-error.json` (`{timestamp, script, error, dump?}`) and deletes it on the next success, so **the file's existence means the channel is broken**. CI commits this file when a job fails.

1. **Discover**: check `reference/*/last-error.json` (a dirty `git status` on the repo, or a failed watch-pipeline run).
2. **Diagnose**: read `error`; if a `dump` path is present, inspect the dumped upstream page — structure changed.
3. **Adapt**: compare the dumped page against the channel's `extra.json` contract; fix the selectors/parser in the channel's script (e.g. `parse_tables`/`col_index` in watch_goat.py, `_find_entries_array` in watch_arena.py).
4. **Verify offline**: replay the dump through the script and extend `watch-pipeline/tests/fixtures/` with a representative excerpt:
   ```bash
   python3 watch-pipeline/scripts/watch_goat.py --html watch-pipeline/reference/goat/failed-page.html \
     --all-models data/all_models.json --output /tmp/goat.json
   python3 -m pytest watch-pipeline/tests -q
   ```
5. **Commit**: the fix together with the updated fixture. The success run in CI removes `last-error.json`; do not hand-edit it away without a verified fix.

## Snapshot invariants

- `goat-models.json` / `opencode-go-models.json` are api.json-shaped provider envelopes (`{"commandcode-goat": {...}}` / `{"opencode-go": {...}}`); `arena.json` is flat `{schema_version, source, models}`.
- GOAT enriches from the single source `data/all_models.json` (never from `opencode-go-models.json`); GOAT-exclusive ids without an upstream base are dropped.
- `watch_go.py` enriches `data/opencode-go-models.json` in place — no separate `go.json`; Go-only ids are skipped, stale Go fields are cleared.
- `watch_arena.py` owns only `data/arena.json`; joining Arena ids to OpenCode ids happens in `models-mapping`, never here.
- Success messages: `watch-arena: wrote N models ...` / `watch-go: enriched N Go records ...` / `watch-goat: N models -> ...`.

See `docs/adr/0006-goat-as-fourth-snapshot.md` for the GOAT ownership boundary.
