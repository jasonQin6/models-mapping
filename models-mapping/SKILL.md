---
name: models-mapping
description: Compute claude/gpt → opencode model mappings using arena data.
---

# models-mapping

计算最优 claude/gpt → opencode 映射，使用 arena 排行榜数据。

## Architecture

两个脚本各自维护 models.csv 的不同列：

- **parse_opencode_mdx.py**: 基础数据列（定价、配额）
- **watch_arena.py**: arena 列（评分、排名、组织）
- **compute_mapping.py**: mapping 列（request → opencode 映射）

## Workflows

1. **watch-opencode.yml** (every 4 hours): 解析 go.mdx → 获取 arena 数据
2. **watch-arena.yml** (daily UTC 23:00): 获取 arena 数据
3. **compute-mapping.yml** (triggered/manual): 计算映射

## Arena Data Fallback

1. Direct match
2. Remove -contributor suffix
3. Version downgrade
4. Prefix match
5. Free model default

## models.csv Structure

31 rows (22 opencode + 9 request), sorted by arena_score.

加权列靠前：arena_score, arena_rank, rp5h, usage_quota, price_output, max_price_output

## Manual Execution

```bash
# Arena 数据
python3 scripts/watch_arena.py

# 计算映射
python3 scripts/compute_mapping.py --stdout
```

## Scoring Formula

见 `data/formula.md`。
