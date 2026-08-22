# Data Schema

## models.csv (root)

OpenCode model data, maintained by GitHub Actions workflow.

| Column | Type | Description |
|--------|------|-------------|
| model_id | string | Standardized model identifier |
| name | string | Display name |
| protocol | enum | completions / messages / responses |
| rp5h | int | Requests per 5 hours |
| usage_quota | float | Dollar usage quota |
| price_output | float | Output price per 1M tokens (cheapest variant) |
| max_price_output | float | Highest output price per 1M tokens |
| rpw | int | Requests per week |
| rpm | int | Requests per month |
| price_input | float | Input price per 1M tokens |
| price_cached_read | float | Cached read price per 1M tokens |
| price_cached_write | float | Cached write price per 1M tokens |
| context_threshold | string | Context length threshold (272K, 256K, -) |
| peak_hours | string | Peak hours for time-based pricing |
| retention | int | Data retention in days (0 = ZDR, 999 = Not ZDR) |

## leaderboard.json

Arena leaderboard data, fetched by `fetch_data.py` (Mac only).

| Key | Type | Description |
|-----|------|-------------|
| model_id | string | Normalized model identifier |
| effort | string? | max/xhigh/high/medium/low or null |
| rank | int | Arena rank |
| rating | float | Arena score |
| context | int/string | Context length or "-" |
| organization | string | Model organization |

## mapping-{YYMMDD}.csv

Output of `compute_mapping.py`.

| Column | Description |
|--------|-------------|
| request_model | Claude/GPT model name |
| target_model | opencode model ID |
| protocol | completions / messages / responses |
| src_score | source model arena score |
| target_score | target model arena score |
| RP5H | target model requests per 5 hours |
| price_out | target model output price |
| retention | data retention in days |
| effort | max/xhigh/high/medium/low/- |
| usage_quota | target model dollar quota |
