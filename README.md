# models-mapping

Monitor [OpenCode Go](https://opencode.ai/docs/go/) model changes and compute optimal claude/gpt → opencode model mappings using arena leaderboard data.

## Architecture

三个脚本各自维护 models.csv 的不同列：

1. **parse_opencode_mdx.py**：维护 opencode 模型的基础数据列（定价、配额等）
2. **watch_arena.py**：维护 arena 相关列（评分、排名、组织等）
3. **compute_mapping.py**：维护 mapping 列（request 模型 → opencode 模型映射）

**Fallback handler**：当脚本无法填充某些列（存在空值）时，使用 `models-mapping` skill 指导 agent 介入。

## Workflows

1. **watch-opencode.yml** (every 4 hours): 检查 Atom feed → 解析 go.mdx → 获取 arena 数据 → 计算映射
2. **watch-arena.yml** (daily UTC 23:00 = UTC+8 07:00): 获取 arena 数据
3. **compute-mapping.yml** (triggered by watch-opencode): 计算映射

## models.csv Structure

31 rows (22 opencode + 9 request), sorted by arena_score descending.

### Column Order

加权列靠前：

| Column | Description |
|--------|-------------|
| model_id | 模型标识符 |
| provider | "opencode" 或 arena organization |
| protocol | completions / messages / responses |
| arena_score | Arena 评分 |
| arena_rank | Arena 排名 |
| rp5h | 每 5 小时请求数 |
| usage_quota | 美元配额 |
| price_output | 输出价格（最便宜变体） |
| max_price_output | 最高输出价格 |
| rpw, rpm | 每周/月请求数 |
| price_input, price_cached_read, price_cached_write | 其他价格 |
| context_threshold | 上下文长度阈值 |
| peak_hours | 高峰时段 |
| retention | 数据保留天数 |
| arena_context, organization, effort | Arena 元数据 |
| mapping | 目标 opencode 模型（仅 request 模型） |

### Row Types

- **Opencode models (22)**: provider="opencode", 有定价数据, mapping 为空
- **Request models (9)**: provider=arena org, 无定价数据, mapping 指向目标

## Local Usage

```bash
# Parse .mdx → models.csv (基础数据)
python3 scripts/parse_opencode_mdx.py go.mdx --output models.csv

# Fetch arena data (arena 列)
python3 scripts/watch_arena.py

# Compute mapping (mapping 列)
python3 scripts/compute_mapping.py

# Check for empty values (trigger fallback handler)
python3 -c "
import csv
with open('models.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        empty_cols = [k for k in ['arena_score', 'rp5h', 'usage_quota', 'price_output'] if not row.get(k)]
        if empty_cols:
            print(f\"{row['model_id']}: {', '.join(empty_cols)}\")
"
```

## Related Skills

- **models-mapping**: Fallback handler for data gaps（当 models.csv 存在空值时使用）
- **axonhub-config**: 应用映射到 AxonHub（服务器端）
