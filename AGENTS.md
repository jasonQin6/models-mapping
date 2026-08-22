# Repository Guidelines

## Project Structure

```
.
├── scripts/
│   ├── parse_opencode_mdx.py       # go.mdx → models.csv (watch-opencode.yml)
│   ├── fetch_data.py               # Arena leaderboard → data/leaderboard.json (compute-mapping.yml)
│   └── compute_mapping.py          # models.csv + leaderboard → data/mapping-*.csv
├── data/                           # Runtime data (auto-updated by workflows)
│   ├── leaderboard.json            # Arena leaderboard data
│   ├── leaderboard.hash            # Hash for change detection
│   ├── mapping-*.csv               # Computed mappings
│   └── formula.md                  # Scoring formula documentation
├── models.csv                      # OpenCode model data (auto-updated)
├── go.mdx                          # Reference copy of source document
├── .github/workflows/
│   ├── watch-opencode.yml          # Every 15 min: go.mdx → models.csv
│   └── compute-mapping.yml         # Every 4 hours: arena → mapping
├── models-mapping/                 # Skill: documentation only
│   └── SKILL.md
└── axonhub-config/                 # Skill: apply mappings (server only)
```

## Data Flow

1. **watch-opencode.yml** (every 15 min): polls Atom feed → if changed → fetch `go.mdx` → parse → update `models.csv`
2. **compute-mapping.yml** (every 4 hours or on models.csv change): fetch arena data → compute mapping → output `data/mapping-{YYMMDD}.csv`
3. **Manual**: Post mapping CSV to Multica issue for user confirmation
4. **Server agent**: Apply confirmed mapping to AxonHub via `axonhub-config` skill

## Commands

```bash
# Parse .mdx → models.csv
python3 scripts/parse_opencode_mdx.py go.mdx --output models.csv

# Fetch arena data (exit 0 = changed, exit 2 = no changes)
python3 scripts/fetch_data.py

# Compute mapping (reads models.csv + data/leaderboard.json)
python3 scripts/compute_mapping.py --output data/mapping-$(date +%y%m%d).csv

# Apply mapping to AxonHub (always dry-run first)
python3 axonhub-config/scripts/apply_mapping.py --axonhub-url <URL> --token <JWT> --dry-run
```

## Coding Style

- Python 3.12+, stdlib only, HTTP via `curl`
- 4-space indent, `snake_case`, type hints on signatures
- Scripts are self-contained: no cross-skill imports
- No external dependencies

## Constraints

- GitHub Actions can access lmarena.ai (tested and verified)
- `axonhub-config` runs on **server only** — requires valid JWT
- Never commit tokens; use GitHub Secrets or environment variables
