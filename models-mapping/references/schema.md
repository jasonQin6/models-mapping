# Data Schema

## opencode-list.json

| Key | Type | Description |
|-----|------|-------------|
| name | string | Display name |
| protocol | enum | completions / messages / responses (from endpoint URL) |
| rp5h | int | Requests per 5 hours |
| rpw | int | Requests per week |
| rpm | int | Requests per month |
| usage_quota | int | Dollar quota |
| price_out | float | Output price per 1M tokens |
| retention | int | Data retention in days (0 = ZDR, 365 = 非 ZDR) |

## leaderboard.json

| Key | Type | Description |
|-----|------|-------------|
| model_id | string | Normalized model identifier |
| effort | string? | max/xhigh/high/medium/low or null |
| rank | int | Arena rank |
| rating | float | Arena score |
| context | int/string | Context length or "-" |
| organization | string | Model organization |

## mapping-{YYMMDD}.csv

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
