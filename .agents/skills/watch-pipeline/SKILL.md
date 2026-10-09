---
name: watch-pipeline
description: Maintain the collection layer — the watch_* scrapers behind .github/workflows/watch-pipeline.yml (daily: models-dev, provider-conf, opencode-go) and watch-arena.yml (weekly: Arena). Adapt selectors when an upstream page changes, guided by the script's own parser and the persisted last-error.json. Pipeline execution belongs to CI; agents do not run full scrapes against live upstreams.
---

# Watch Pipeline

This skill is the maintenance manual for the collection layer. It owns `.agents/skills/watch-pipeline/scripts/` (the `watch_*.py` scrapers plus `models_extra.py`, the shared models-extra store, and `error_state.py`), their tests, and the per-channel `reference/` directories. It does NOT own the compute layer (that is axonhub-admin's offline step scripts) and never writes AxonHub. Collection stores raw facts only: name normalization, matching, and derived-value backfill (free quotas, prices) live in axonhub-admin's offline scripts.

## Channels

| Channel | Script | Upstream | Snapshot | Failure trail |
|---|---|---|---|---|
| models-dev | *(pure `curl` in yml, no script)* | models.dev/models.json | `data/all_models.json` | — |
| provider-conf | *(pure `curl` in yml, no script)* | PublicProviderConf `dist/all.json` (dev branch raw) | `data/provider_conf.json` | — |
| opencode-go | `scripts/watch_go.py` | opencode `go.mdx` | `data/models_extra.json` (`channels.opencode-go`) | `reference/go/` |
| goat *(已退役)* | `scripts/watch_goat.py` | commandcode.ai GOAT plan page | — | — |
| arena | `scripts/watch_arena.py` | lmarena.ai WebDev leaderboard | `data/arena.json` | `reference/arena/` |

Arena runs **weekly** (watch-arena.yml, Monday UTC 03:00, manual dispatch anytime): the leaderboard moves slowly, the crawler is the most brittle of the channels, and the mapping formula does not need fresher scores. `watch-pipeline.yml` no longer schedules it.

goat 渠道退役（2026-09-30）：Command Code Goat 订阅取消，goat 采集 job 已从
workflow 下线，`channels.commandcode-goat` 节已随退役从快照移除；
`watch_goat.py` 与其测试/fixture 仅作本地调试保留，若恢复订阅需重建
workflow job 并按其 field contract 重新验收。

Execution entrypoints are `.github/workflows/watch-pipeline.yml` (daily cron) and `.github/workflows/watch-arena.yml` (weekly cron). Every script is stdlib-only Python 3.12+ and replays offline: `watch_go.py` takes a positional go.mdx path, `watch_goat.py` takes `--html`, and `watch_arena.py` takes `--input`.

## Field contracts

Each scraper's parse function is the single definition of what it extracts — which upstream column/field each record field comes from and where it lands in `data/models_extra.json` or `data/arena.json`. When adapting a script, treat the script as the contract; there is no separate contract file. The store shape never changes here; changing it is a structural decision.

## Error repair workflow

Each script persists failures to `reference/<channel>/last-error.json` inside this skill directory (`{timestamp, script, error, dump?}`) and deletes it on the next success, so **the file's existence means the channel is broken**. The directory is created by the first failure and absent in a healthy repository. CI commits this file when a job fails and commits its removal on the next success.

1. **Discover**: check `reference/*/last-error.json` (a dirty `git status` on the repo, or a failed watch-pipeline run).
2. **Diagnose**: read `error`; if a `dump` path is present, inspect the dumped upstream page — structure changed.
3. **Adapt**: compare the dumped page against the channel script's parser; fix the selectors (e.g. `parse_tables`/`col_index` in watch_goat.py, `_find_entries_array` in watch_arena.py).
4. **Verify offline**: replay the dump through the script and extend `tests/fixtures/` with a representative excerpt:
   ```bash
   python3 .agents/skills/watch-pipeline/scripts/watch_goat.py \
     --html .agents/skills/watch-pipeline/reference/goat/failed-page.html \
     --extra /tmp/models_extra.json
   python3 -m pytest .agents/skills/watch-pipeline/tests -q
   ```
5. **Commit**: the fix together with the updated fixture. The success run in CI removes `last-error.json`; do not hand-edit it away without a verified fix.

## Snapshot invariants

- `data/models_extra.json` is the shared store `{schema_version, updated_at, channels}`; each collector updates **only its own** `channels.<channel>` section via `models_extra.update_channel` (read-modify-write, field-level merge, atomic), so writers must serialize — CI orders goat after go. Contract fields refresh in place; hand-maintained fields on surviving records are preserved, and ids that left the channel declaration are removed with their record.
- Sections carry channel-declared facts only (`rp5h`, `usage_quota`, `cost{}`, goat `tok_s`); card data is never collected here — axonhub-admin's offline scripts fill it from the provider-conf overlay (`data/provider_conf.json`) with `data/all_models.json` as fallback. Freeness has no marker of its own: the watchers transcribe their channels' free price wording (`Free` cells in go.mdx, `free` on GOAT) into zero prices — an ordinary contract value an upstream rescission simply refreshes — and the compute layer reads prices only; no free flag or id-suffix heuristic exists anywhere. Speed-marketing variants (`-fast`/`-highspeed`) are excluded by axonhub-admin's materialized `speed:` class, not by collection: exclusions live in axonhub-admin's `data/blocklist.json`, which this layer neither reads nor writes — collection stores no exclusion state, so a delete/re-add cycle cannot lose one.
- Channel-provided `claude-*` models carry no special status: they are collected like any other id (rule removed 2026-10-09); request-side Claude global models and arena mapping live in axonhub-admin.
- The go section's keys are exactly the go.mdx model ids; an id that leaves the document leaves the section.
- The goat section's keys are the channel's entitlement facts; GOAT hard gates: main-table rows skipped for missing columns, `to_model_id` collisions, or zero models fail the run with no write (partial lists must never publish).
- `data/provider_conf.json` is the verbatim `all.json` aggregate (git stores content-addressed objects, so daily snapshots only grow by their actual delta); no projection or normalization happens here — axonhub-admin's `snapshot.load_provider_conf` does the vendor/canonical aggregation offline.
- `watch_arena.py` owns only `data/arena.json`; joining Arena ids to OpenCode ids happens in `axonhub-admin`'s offline scripts (`registry.py`), never here.
- Success messages: `watch-arena: wrote N models ...` / `watch-go: N Go models -> ...` / `watch-goat: N models -> ...`.

## CI security

- Third-party Actions are pinned to reviewed versions or commit SHAs with minimal permissions (typically `contents: write`); credentials enter only via GitHub Secrets, and the workflow never holds AxonHub credentials.
- Committing is serialized by `concurrency` per output file; each job writes only its own snapshot.
- Never interpolate scraped page content, commit messages, or branch names into shell commands.
- CI runs no tests — the schema/hard-gate checks are built into the scripts themselves (before write); maintainers run `pytest` locally.
