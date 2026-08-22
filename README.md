# models-mapping

Monitor [OpenCode Go](https://opencode.ai/docs/go/) model changes and maintain an up-to-date `models.csv` with pricing, rate limits, and metadata.

## What this does

A GitHub Actions workflow polls the [opencode go.mdx Atom feed](https://github.com/anomalyco/opencode/commits/dev/packages/web/src/content/docs/go.mdx.atom) every 15 minutes. When the source document changes, it fetches the updated `.mdx` file, parses four markdown tables (usage limits, pricing, endpoints, privacy), and commits the updated `models.csv`.

## Output: models.csv

| Column | Description |
|--------|-------------|
| model_id | Standardized model identifier (e.g. `kimi-k3`) |
| name | Display name |
| protocol | API protocol: `completions` / `messages` / `responses` |
| rp5h | Requests per 5 hours |
| rpw | Requests per week |
| rpm | Requests per month |
| usage_quota | Dollar usage quota |
| price_input | Input price per 1M tokens |
| price_output | Output price per 1M tokens |
| price_cached_read | Cached read price per 1M tokens |
| price_cached_write | Cached write price per 1M tokens |
| retention | Data retention in days (0 = ZDR) |
| retention_note | Additional retention notes |
| model_training | Whether data is used for model training |

## Local usage

```bash
# Parse .mdx from stdin
curl -sL "https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/web/src/content/docs/go.mdx" | python3 scripts/parse_opencode_mdx.py

# Parse local file
python3 scripts/parse_opencode_mdx.py /path/to/go.mdx --output models.csv
```

## Project structure

```
.
├── .github/workflows/watch-opencode.yml  # Atom feed monitor
├── .opencode-commit-hash                 # Latest known commit hash
├── models.csv                            # Parsed model data
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
