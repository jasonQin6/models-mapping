# Repository Guidelines

## Project Structure

```
.
├── scripts/
│   ├── parse_opencode_mdx.py       # go.mdx → models.csv (opencode 模型基础数据)
│   ├── watch_arena.py              # Arena 数据 → models.csv (arena 相关列)
│   └── compute_mapping.py          # models.csv → 更新 mapping 列
├── data/                           # Runtime data (auto-updated by workflows)
├── models.csv                      # Merged model data (opencode + request models)
├── go.mdx                          # Reference copy of source document
├── .opencode-commit-hash           # Latest known commit hash for go.mdx
├── .github/workflows/
│   ├── watch-opencode.yml          # Every 4 hours: parse go.mdx → watch_arena
│   ├── watch-arena.yml             # Daily UTC 23:00 (UTC+8 07:00): watch_arena
│   └── compute-mapping.yml         # Triggered by watch-opencode: compute_mapping
├── models-mapping/                 # Skill: fallback handler for data gaps
│   └── SKILL.md
└── axonhub-config/                 # Skill: apply mappings (server only)
```

## Script Responsibilities

### parse_opencode_mdx.py
维护 models.csv 的 opencode 模型基础数据列：
- rp5h, usage_quota, price_output, max_price_output
- rpw, rpm, price_input, price_cached_read, price_cached_write
- context_threshold, peak_hours, retention

### watch_arena.py
维护 models.csv 的 arena 相关列：
- arena_score, arena_rank, arena_context
- organization, effort

对所有已存在的模型（opencode + request）更新 arena 数据。

内置 fallback 策略：
1. Direct match
2. Remove -contributor suffix
3. Version downgrade
4. Prefix match with wildcard
5. Free model default

### compute_mapping.py
维护 models.csv 的 mapping 列：
- 从 CSV 读取所有 request 模型（provider != "opencode"）
- 按系列分组（claude-*, gpt-*）
- 每个系列中 arena_score 最低的模型作为 cheap 模型
- Cheap 模型路由到 free opencode 模型或最高配额模型
- 其他模型使用 proximity-based scoring formula

## Fallback Handler

当脚本无法填充某些列（存在空值）时，使用 `models-mapping` skill 指导 agent 介入：
- arena_score 空：应用版本降级、去除后缀等策略
- rp5h/usage_quota 空：使用同类模型中位数
- price_output 空：从 go.mdx 获取或标记待人工确认

## Workflow

1. **watch-opencode.yml** (every 4 hours):
   - 检查 Atom feed 是否有新 commit
   - 如果有变化：parse_opencode_mdx.py → watch_arena.py → compute_mapping.py

2. **watch-arena.yml** (daily UTC 23:00 = UTC+8 07:00):
   - watch_arena.py

3. **compute-mapping.yml** (triggered by watch-opencode):
   - compute_mapping.py

## models.csv Structure

31 rows (1 header + 22 opencode models + 9 request models), sorted by arena_score descending.

### Column Order

加权列（参与评分计算）靠前：

```
model_id, provider, protocol,
arena_score, arena_rank, rp5h, usage_quota, price_output, max_price_output,
rpw, rpm, price_input, price_cached_read, price_cached_write,
context_threshold, peak_hours, retention,
arena_context, organization, effort,
mapping
```

### Row Types

**Opencode models (22 rows)**:
- provider = "opencode"
- 有完整的定价和配额数据
- mapping 列为空

**Request models (9 rows)**:
- Claude/GPT 模型（用户维护）
- provider = arena organization (Anthropic, OpenAI)
- 有 arena 数据但没有定价/配额数据
- mapping 列指向目标 opencode 模型

## Commands

```bash
# Parse .mdx → models.csv (opencode 基础数据)
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

# Apply mapping to AxonHub (always dry-run first)
python3 axonhub-config/scripts/apply_mapping.py --axonhub-url <URL> --token <JWT> --dry-run
```

## Coding Style

- Python 3.12+, stdlib only, HTTP via curl
- 4-space indent, snake_case, type hints on signatures
- Scripts are self-contained: no cross-skill imports
- No external dependencies

## Constraints

- GitHub Actions can access lmarena.ai (tested and verified)
- axonhub-config runs on server only — requires valid JWT
- Never commit tokens; use GitHub Secrets or environment variables
