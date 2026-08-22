---
name: models-mapping
description: Fetch arena data and compute claude/gpt → opencode model mappings. Runs on Mac only.
---

# models-mapping

Fetches arena leaderboard and opencode model data, computes optimal claude/gpt → opencode mappings using proximity-based scoring, outputs CSV for user review.

**Runs on Mac only** — arena.ai blocks datacenter IPs.

## Automated Watch (Recommended)

`scripts/watch_opencode.sh` polls the opencode source doc on GitHub for changes. When the `.mdx` file changes, it triggers `codex exec` to run the full pipeline.

```bash
# Check once (no LLM cost)
./scripts/watch_opencode.sh --dry-run

# Check + trigger codex if changed
./scripts/watch_opencode.sh

# Force trigger regardless of hash
./scripts/watch_opencode.sh --force
```

### Daily schedule via launchd

```bash
cp scripts/com.axonhub.watch-opencode.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.axonhub.watch-opencode.plist
```

Runs daily at 09:00. Logs in `references/watch.log`.

**Data source:** `https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/zh-cn/go.mdx`

## Manual Workflow

1. `python3 scripts/fetch_data.py`
   - Completion: exits 0 (data changed) or 2 (no changes)
   - Default: fetch opencode → if changed → fetch arena
   - `--skip hash`: skip hash detection, force fetch both
   - `--skip fetch`: skip all fetching, use existing cache
   - If exit 2, stop — no downstream work needed

2. `python3 scripts/compute_mapping.py --stdout`
   - Completion: CSV written to `references/mapping-{YYMMDD}.csv`
   - Review CSV output

3. Post CSV to Multica issue for user confirmation
   - Completion: user confirms in issue

4. Handoff: server agent runs `axonhub-config` with the CSV

## Quick Reference

| Operation | Command | Trigger |
|-----------|---------|---------|
| Watch + auto-trigger | `scripts/watch_opencode.sh` | launchd daily or manual |
| Watch dry-run | `scripts/watch_opencode.sh --dry-run` | test without LLM |
| Fetch data (default) | `python3 scripts/fetch_data.py` | manual |
| Force fetch both | `python3 scripts/fetch_data.py --skip hash` | when cache is stale |
| Skip fetching | `python3 scripts/fetch_data.py --skip fetch` | recompute with existing cache |
| Compute mapping | `python3 scripts/compute_mapping.py --stdout` | after fetch_data.py exits 0 |

## Error Handling

| Error | Action |
|-------|--------|
| Network failure | Retry after 1 hour |
| Arena 404 | Check URL; Cloudflare may have updated rules |
| Empty cache | Run `fetch_data.py --skip hash` |
| watch_opencode.sh fails | Check `references/watch.log` |

## Common Mistakes

- Running fetch_data without `--skip hash` when cache is stale → hash-based detection skips if unchanged
- Running compute_mapping without fresh data → always run fetch_data first
- Forgetting to post CSV to Multica issue → the CSV is the handoff artifact

## References

- [Scoring formula and weights](references/formula.md)
- [Data schema (JSON + CSV)](references/schema.md)
