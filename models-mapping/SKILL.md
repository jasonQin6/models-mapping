---
name: models-mapping
description: Compute claude/gpt → opencode model mappings using arena data. Scripts moved to root scripts/.
---

# models-mapping

Computes optimal claude/gpt → opencode mappings using proximity-based scoring on arena leaderboard data.

## Architecture

Scripts have been moved to the root `scripts/` directory and are orchestrated by GitHub Actions:

- `scripts/fetch_data.py` — Fetches arena leaderboard data (runs every 4 hours)
- `scripts/compute_mapping.py` — Computes mapping from models.csv + arena data
- `scripts/parse_opencode_mdx.py` — Parses opencode go.mdx → models.csv (separate workflow)

## Data Flow

1. **watch-opencode.yml** (every 15 min): polls Atom feed → if changed → fetch `go.mdx` → parse → update `models.csv`
2. **compute-mapping.yml** (every 4 hours or on models.csv change): fetch arena data → compute mapping → output `data/mapping-{YYMMDD}.csv`
3. **Manual**: Post mapping CSV to Multica issue for user confirmation
4. **Server agent**: Apply confirmed mapping to AxonHub via `axonhub-config` skill

## Data Locations

- `models.csv` — OpenCode model data (auto-updated by watch-opencode.yml)
- `data/leaderboard.json` — Arena leaderboard data (auto-updated by compute-mapping.yml)
- `data/mapping-{YYMMDD}.csv` — Computed mappings (output of compute_mapping.py)

## Manual Execution

```bash
# Fetch arena data (exit 0 = changed, exit 2 = no changes)
python3 scripts/fetch_data.py

# Compute mapping
python3 scripts/compute_mapping.py --stdout
```

## Scoring Formula

See `data/formula.md` for the proximity-based scoring algorithm.

## Error Handling

- Arena fetch fails → Check if lmarena.ai is accessible (GitHub Actions can access it)
- models.csv missing → Check watch-opencode.yml workflow status
- Empty leaderboard → Run fetch_data.py manually
