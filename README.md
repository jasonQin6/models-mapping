# models-mapping

Monitor [OpenCode Go](https://opencode.ai/docs/go/) model changes and maintain an up-to-date `models.csv` with pricing, rate limits, and metadata.

## What this does

A GitHub Actions workflow polls the [opencode go.mdx Atom feed](https://github.com/anomalyco/opencode/commits/dev/packages/web/src/content/docs/go.mdx.atom) every 15 minutes. When the source document changes, it fetches the updated `.mdx` file, parses four markdown tables (usage limits, pricing, endpoints, privacy), and commits the updated `models.csv`.

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

## Local usage

```bash
# Parse .mdx from stdin
curl -sL "https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/go.mdx" | python3 scripts/parse_opencode_mdx.py

# Parse local file
python3 scripts/parse_opencode_mdx.py go.mdx --output models.csv
```

## Project structure

```
.
├── .github/workflows/watch-opencode.yml  # Atom feed monitor
├── .opencode-commit-hash                 # Latest known commit hash
├── models.csv                            # Parsed model data (22 models)
├── go.mdx                                # Reference copy of source document
├── scripts/
│   └── parse_opencode_mdx.py            # .mdx parser
├── models-mapping/                       # Skill: arena-based mapping
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── fetch_data.py                # Arena + opencode fetcher (Mac only)
│   │   ├── compute_mapping.py           # Proximity-based model mapping
│   │   └── watch_opencode.sh            # Local launchd watch script
│   └── references/                      # Cached data and outputs
└── axonhub-config/                       # Skill: apply mappings to AxonHub
    └── SKILL.md
```

## Related skills

- **models-mapping**: Fetches arena leaderboard data and computes optimal claude/gpt → opencode model mappings using proximity-based scoring. Runs on Mac only (arena blocks datacenter IPs).
- **axonhub-config**: Applies model mappings to AxonHub channels/associations. Runs on server.

## Future work

- **Arena data merge**: Separate script to merge arena leaderboard data (rank, rating, context, organization, effort) into models.csv using fuzzy matching on model_id.
- **Data validation**: Agent-based validation via models-mapping skill to verify data quality periodically.
