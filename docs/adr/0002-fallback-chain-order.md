---
status: superseded by ADR-0005
---

# Arena model matching fallback chain order

> Historical decision. Its fallback ordering remains useful when joining Arena
> records, but the current source contract and mapping boundary are defined by
> ADR-0005.

The `find_best_match()` function in `scripts/name_matching.py` implements a 5-layer fallback chain for matching CSV model_ids to arena leaderboard entries. The order is deliberate:

1. **Direct match** — exact normalized name match
2. **Remove -contributor suffix** — handles contributor variants (e.g., muse-spark-1.2-contributor → muse-spark-1.2)
3. **Version downgrade** — tries previous minor version (e.g., qwen3.7-plus → qwen3.6-plus)
4. **Prefix match with wildcard** — matches by prefix, picks highest rating (e.g., claude-haiku → claude-haiku-*)
5. **Free model default** — returns zero-score entry for free models when no match found

**Why this order:** Direct match is most reliable. Contributor suffix is a common opencode naming pattern. Version downgrade handles cases where arena hasn't updated to the latest version. Prefix match is a last resort before giving up. Free model default prevents free models from being unmapped.

**Why not different:** Reordering would change matching behavior. For example, putting prefix match before version downgrade would match claude-3.5-sonnet to claude-3-opus (prefix "claude-3") instead of trying claude-3.4-sonnet first.
