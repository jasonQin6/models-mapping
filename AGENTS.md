# Repository Guidelines

## Project Structure

```
.
├── scripts/
│   ├── parse_opencode_mdx.py       # go.mdx → models.csv (opencode 模型基础数据)
│   ├── watch_arena.py              # Arena 数据 → models.csv (arena 相关列)
│   └── compute_mapping.py          # models.csv → data/mapping-*.csv
├── data/                           # Runtime data (auto-updated by workflows)
│   └── mapping-*.csv               # Computed mappings
├── models.csv                      # Merged model data (opencode + arena + request models)
├── go.mdx                          # Reference copy of source document
├── .opencode-commit-hash           # Latest known commit hash for go.mdx
├── .github/workflows/
│   ├── watch-opencode.yml          # Every 4 hours: check Atom feed → parse go.mdx → watch_arena
│   ├── watch-arena.yml             # Daily UTC 23:00 (UTC+8 07:00): watch_arena
│   └── compute-mapping.yml         # Manual or triggered by watch-opencode
├── models-mapping/                 # Skill: documentation only
│   └── SKILL.md
└── axonhub-config/                 # Skill: apply mappings (server only)
```

## Data Flow

两个脚本各自维护 models.csv 的不同列：

1. **parse_opencode_mdx.py**：维护 opencode 模型的基础数据列
   - rp5h, usage_quota, price_output, max_price_output
   - rpw, rpm, price_input, price_cached_read, price_cached_write
   - context_threshold, peak_hours, retention

2. **watch_arena.py**：维护 arena 相关列
   - arena_score, arena_rank, arena_context
   - organization, effort
   - provider (opencode 或 arena organization)
   - 添加 request 模型（claude/gpt）

3. **compute_mapping.py**：维护 mapping 列
   - 为 request 模型计算最优 opencode 映射

## Workflow

1. **watch-opencode.yml** (every 4 hours):
   - 检查 Atom feed 是否有新 commit
   - 如果有变化：解析 go.mdx → 更新 models.csv（基础数据列）
   - 然后运行 watch_arena.py → 更新 models.csv（arena 列）
   - 如果加权列变化，触发 compute-mapping.yml

2. **watch-arena.yml** (daily UTC 23:00 = UTC+8 07:00):
   - 运行 watch_arena.py → 更新 models.csv（arena 列）

3. **compute-mapping.yml** (triggered or manual):
   - 运行 compute_mapping.py → 更新 models.csv（mapping 列）

## Arena Data Fallback Strategies

当 opencode 模型没有直接匹配的 arena 数据时，按以下顺序尝试：

1. **Direct match**: 精确匹配 model_id
2. **Remove -contributor suffix**: 例如 muse-spark-1.2-contributor → muse-spark-1.2
3. **Version downgrade**: 例如 qwen3.7-plus → qwen3.6-plus
4. **Prefix match with wildcard**: 例如 claude-haiku → claude-haiku-*
5. **Free model default**: arena_score=0（适用于 model_id 包含 "free" 的模型）

某些模型故意没有 arena 数据（例如 deepseek-v4-flash-vision-exp 与 deepseek-v4-flash 是不同模型）。

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
- Claude/GPT 模型（opencode 未提供）
- provider = arena organization (Anthropic, OpenAI)
- 有 arena 数据但没有定价/配额数据
- mapping 列指向目标 opencode 模型

**注意**: gpt-5.6-luna 由 opencode 提供，所以只作为 opencode 模型出现。

## Commands

```bash
# Parse .mdx → models.csv (opencode 基础数据)
python3 scripts/parse_opencode_mdx.py go.mdx --output models.csv

# Fetch arena data and merge (arena 列)
python3 scripts/watch_arena.py

# Compute mapping (mapping 列)
python3 scripts/compute_mapping.py --output data/mapping-$(date +%y%m%d).csv

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
