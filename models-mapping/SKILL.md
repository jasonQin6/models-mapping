---
name: models-mapping
description: Fetch arena data and compute claude/gpt → opencode model mappings. Runs on Mac only.
---

# models-mapping

Fetches arena leaderboard data, combines with `models.csv` (maintained by GitHub Actions), computes optimal claude/gpt → opencode mappings using proximity-based scoring, outputs CSV for user review.

**Runs on Mac only** — arena.ai blocks datacenter IPs.

## Prerequisites

`models.csv` at repo root is maintained by GitHub Actions workflow (`watch-opencode.yml`). It contains opencode model data parsed from `go.mdx`.

## Workflow

1. `python3 scripts/fetch_data.py`
   - Completion: exits 0 (arena data changed) or 2 (no changes)
   - Fetches arena leaderboard (top 100)
   - If exit 2, stop — no downstream work needed

2. `python3 scripts/compute_mapping.py --stdout`
   - Completion: CSV written to `references/mapping-{YYMMDD}.csv`
   - Reads `models.csv` (opencode data) + `references/leaderboard.json` (arena data)
   - Review CSV output

3. Post CSV to Multica issue for user confirmation
   - Completion: user confirms in issue

4. Handoff: server agent runs `axonhub-config` with the CSV

## Quick Reference

| Operation | Command | Trigger |
|-----------|---------|---------|
| Fetch arena data | `python3 scripts/fetch_data.py` | manual or scheduled |
| Compute mapping | `python3 scripts/compute_mapping.py --stdout` | after fetch_data.py exits 0 |

## Error Handling

| Error | Action |
|-------|--------|
| Arena 404 | Check URL; Cloudflare may have updated rules |
| Empty leaderboard cache | Run `fetch_data.py` to refresh |
| models.csv missing | Check GitHub Actions workflow status |

## References

- [Scoring formula](references/formula.md)
- [Data schema](references/schema.md)
