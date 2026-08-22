# Repository Guidelines

## Project Structure

```
.
├── scripts/parse_opencode_mdx.py       # go.mdx → models.csv (GitHub Actions)
├── models.csv                          # OpenCode model data (auto-updated)
├── go.mdx                              # Reference copy of source document
├── .github/workflows/watch-opencode.yml  # Atom feed monitor (every 15 min)
├── models-mapping/                     # Skill: arena fetch + mapping (Mac only)
│   ├── scripts/
│   │   ├── fetch_data.py               # Arena leaderboard fetch only
│   │   └── compute_mapping.py          # Proximity scoring → mapping CSV
│   └── references/                     # Cached arena data, output CSVs
└── axonhub-config/                     # Skill: apply mappings (server only)
```

## Data Flow

1. **GitHub Actions** (every 15 min): polls Atom feed → if changed → fetch `go.mdx` → parse → update `models.csv`
2. **Mac agent** (on trigger): fetch arena data → `compute_mapping.py` reads `models.csv` + arena → output mapping CSV
3. **Server agent** (on confirmation): apply mapping CSV to AxonHub

## Commands

```bash
# Parse .mdx → models.csv
python3 scripts/parse_opencode_mdx.py go.mdx --output models.csv

# Fetch arena data (Mac only, exit 0 = changed, exit 2 = no changes)
python3 models-mapping/scripts/fetch_data.py

# Compute mapping (reads models.csv + leaderboard.json)
python3 models-mapping/scripts/compute_mapping.py --stdout

# Apply mapping to AxonHub (always dry-run first)
python3 axonhub-config/scripts/apply_mapping.py --axonhub-url <URL> --token <JWT> --dry-run
```

## Coding Style

- Python 3.12+, stdlib only, HTTP via `curl`
- 4-space indent, `snake_case`, type hints on signatures
- Scripts are self-contained: no cross-skill imports
- No external dependencies

## Constraints

- `models-mapping` runs on **Mac only** — arena.ai blocks datacenter IPs
- `axonhub-config` runs on **server only** — requires valid JWT
- Never commit tokens; use GitHub Secrets or environment variables
