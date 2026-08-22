# models-mapping

Monitor [OpenCode Go](https://opencode.ai/docs/go/) model changes and compute optimal claude/gpt → opencode model mappings using arena leaderboard data.

## What this does

Two GitHub Actions workflows automate the pipeline:

1. **watch-opencode.yml** (every 15 min): Polls the [opencode go.mdx Atom feed](https://github.com/anomalyco/opencode/commits/dev/packages/web/src/content/docs/go.mdx.atom). When the source document changes, fetches the updated `.mdx` file, parses four markdown tables (usage limits, pricing, endpoints, privacy), and updates `models.csv`.

2. **compute-mapping.yml** (every 4 hours or on models.csv change): Fetches arena leaderboard data from lmarena.ai, combines with `models.csv`, computes optimal mappings using proximity-based scoring, and outputs `data/mapping-{YYMMDD}.csv`.

## Output: models.csv

### Weighted columns (participate in scoring)

| Column | Description |
|--------|-------------|
| rp5h | Requests per 5 hours |
| usage_quota | Dollar usage quota |
| price_output | Output price per 1M tokens (cheapest variant) |
| max_price_output | Highest output price per 1M tokens (most expensive variant) |

### Other columns

| Column | Description |
|--------|-------------|
| model_id | Standardized model identifier (e.g. `kimi-k3`) |
| name | Display name |
| protocol | API protocol: `completions` / `messages` / `responses` |
| rpw | Requests per week |
| rpm | Requests per month |
| price_input | Input price per 1M tokens |
| price_cached_read | Cached read price per 1M tokens |
| price_cached_write | Cached write price per 1M tokens |
| context_threshold | Context length threshold for pricing variants (e.g. `272K`, `256K`, `-`) |
| peak_hours | Peak hours for time-based pricing (e.g. `01:00-04:00 and 06:00-10:00 UTC`, `-`) |
| retention | Data retention in days (`0` = ZDR, `999` = Not ZDR) |

### Pricing variants

Some models have different pricing based on context length or time of day:

- **Context length variants**: GPT 5.6 Luna (≤272K vs >272K), Qwen3.7 Plus (≤256K vs >256K), Qwen3.6 Plus (≤256K vs >256K)
- **Time-based variants**: DeepSeek V4 Pro/Flash/Flash Vision Exp (Off-Peak vs Peak)

The CSV keeps the cheapest variant as `price_output` and records the most expensive as `max_price_output`.

### Free models

Models with `free` in their model_id (e.g. `ox-alpha-free`) get special treatment:
- `rp5h` = max of all other models
- `usage_quota` = max of all other models
- All prices = 0

This logic is in `fix_free_model()` and is called only when free models are detected. Users can manually update the CSV later with actual measured values.

## Project structure

```
.
├── scripts/
│   ├── parse_opencode_mdx.py       # go.mdx → models.csv
│   ├── fetch_data.py               # Arena leaderboard → data/leaderboard.json
│   └── compute_mapping.py          # models.csv + leaderboard → data/mapping-*.csv
├── data/                           # Runtime data (auto-updated)
│   ├── leaderboard.json            # Arena leaderboard data
│   ├── leaderboard.hash            # Hash for change detection
│   ├── mapping-*.csv               # Computed mappings
│   └── formula.md                  # Scoring formula documentation
├── models.csv                      # OpenCode model data (auto-updated)
├── go.mdx                          # Reference copy of source document
├── .github/workflows/
│   ├── watch-opencode.yml          # Every 15 min
│   └── compute-mapping.yml         # Every 4 hours
├── models-mapping/                 # Skill: documentation only
│   └── SKILL.md
└── axonhub-config/                 # Skill: apply mappings (server only)
    └── SKILL.md
```

## Local usage

```bash
# Parse .mdx → models.csv
python3 scripts/parse_opencode_mdx.py go.mdx --output models.csv

# Fetch arena data
python3 scripts/fetch_data.py

# Compute mapping
python3 scripts/compute_mapping.py --output data/mapping-$(date +%y%m%d).csv
```

## Related skills

- **models-mapping**: Documentation for the mapping pipeline. Scripts moved to root `scripts/`.
- **axonhub-config**: Applies model mappings to AxonHub channels/associations. Runs on server.
