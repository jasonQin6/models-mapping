---
name: watch-pipeline
description: Maintain the collection layer — the watch_* scrapers behind .github/workflows/watch-pipeline.yml and their four channels (models-dev, opencode-go, GOAT, Arena). Adapt selectors when an upstream page changes, guided by per-channel field contracts in reference/<channel>/extra.json and the persisted last-error.json. Pipeline execution belongs to CI; agents do not run full scrapes against live upstreams.
---

# Watch Pipeline

This skill is the maintenance manual for the collection layer. It owns `watch-pipeline/scripts/` (the `watch_*.py` scrapers plus `models_extra.py`, the shared models-extra store, and `error_state.py`), their tests, and the per-channel `reference/` directories. It does NOT own `model-registry/` (planning) and never writes AxonHub.

## Channels

| Channel | Script | Upstream | Snapshot | Reference |
|---|---|---|---|---|
| models-dev | *(pure `curl` in yml, no script)* | models.dev/models.json | `data/all_models.json` | — |
| opencode-go | `watch-pipeline/scripts/watch_go.py` | opencode `go.mdx` | `data/models_extra.json` (`channels.opencode-go`) | `reference/go/` |
| goat | `watch-pipeline/scripts/watch_goat.py` | commandcode.ai GOAT plan page | `data/models_extra.json` (`channels.commandcode-goat`) | `reference/goat/` |
| arena | `watch-pipeline/scripts/watch_arena.py` | lmarena.ai WebDev leaderboard | `data/arena.json` | `reference/arena/` |

Execution entrypoint is `.github/workflows/watch-pipeline.yml` (every-3-days cron). Every script is stdlib-only Python 3.12+ and replays offline via `--html` / positional input / `--input` respectively.

## Field contracts

`reference/<channel>/extra.json` is the machine-readable definition of what each scraper extracts: which upstream column/field each record field comes from, its type, parse rule, and where it lands in `data/models_extra.json`. When adapting a script, read the channel's extra.json first — the fix must keep the section matching that contract. The store shape never changes here; changing it is an ADR-level decision.

## Error repair workflow

Each script persists failures to `watch-pipeline/reference/<channel>/last-error.json` (`{timestamp, script, error, dump?}`) and deletes it on the next success, so **the file's existence means the channel is broken**. CI commits this file when a job fails.

1. **Discover**: check `reference/*/last-error.json` (a dirty `git status` on the repo, or a failed watch-pipeline run).
2. **Diagnose**: read `error`; if a `dump` path is present, inspect the dumped upstream page — structure changed.
3. **Adapt**: compare the dumped page against the channel's `extra.json` contract; fix the selectors/parser in the channel's script (e.g. `parse_tables`/`col_index` in watch_goat.py, `_find_entries_array` in watch_arena.py).
4. **Verify offline**: replay the dump through the script and extend `watch-pipeline/tests/fixtures/` with a representative excerpt:
   ```bash
   python3 watch-pipeline/scripts/watch_goat.py --html watch-pipeline/reference/goat/failed-page.html \
     --extra /tmp/models_extra.json
   python3 -m pytest watch-pipeline/tests -q
   ```
5. **Commit**: the fix together with the updated fixture. The success run in CI removes `last-error.json`; do not hand-edit it away without a verified fix.

## Snapshot invariants

- `data/models_extra.json` is the shared store `{schema_version, updated_at, channels}`; each collector rewrites **only its own** `channels.<channel>` section via `models_extra.update_channel` (read-modify-write, atomic), so writers must serialize — CI orders goat after go.
- Sections carry channel-declared facts only (`rp5h`, `usage_quota`, `cost{}`, remark thresholds, goat `tok_s`); card data is never collected here — planning fills it from `data/all_models.json` (ADR 0012).
- Channel-provided `claude-*` models are not collected: Claude is served by self-built AxonHub models mapped by arena score (ADR 0012).
- The go section's keys are exactly the go.mdx model ids (ADR 0011); an id that leaves the document leaves the section.
- The goat section's keys are the channel's entitlement facts; GOAT hard gates: main-table rows skipped for missing columns, `to_model_id` collisions, zero models, or a count outside `expected_count` in `reference/goat/extra.json` fail the run with no write (partial lists must never publish).
- `watch_arena.py` owns only `data/arena.json`; joining Arena ids to OpenCode ids happens in `model-registry`, never here.
- Success messages: `watch-arena: wrote N models ...` / `watch-go: N Go models -> ...` / `watch-goat: N models -> ...`.
