# Mapping scoring formula

`models.csv` is a generated review artifact. It contains only the model ID,
the row role, the Arena score, RP5H and the suggested one-to-one mapping.

For a non-baseline request model, choose the candidate with the highest score:

```text
match =
    0.35 * arena_score
  + 0.30 * log_rp5h
  + 0.35 * proximity
  - downgrade_penalty
  + upgrade_bonus
```

```text
arena_score = candidate_arena_score / max_candidate_arena_score
log_rp5h = log(candidate_rp5h + 1) / log(max_candidate_rp5h + 1)
proximity = 1 - abs(candidate_arena_score - request_arena_score) / max_score_diff
downgrade_penalty = 0.2 * (request_arena_score - candidate_arena_score) / max_score_diff
upgrade_bonus = 0.1
```

`proximity` is `1` when every candidate has the same score. The penalty is
applied only when the candidate score is below the request score, and the
upgrade bonus only when it is above it. Prices and `usage_quota` are source
metadata, not mapping-score dimensions; they are written to AxonHub by the
sync skill.

## Baseline routing

For each `claude-*` or `gpt-*` family, the request model with the lowest valid
Arena score is the baseline. The baseline bypasses the formula and maps to
the eligible free candidate with the highest RP5H. If no free candidate is
available, it maps to the eligible candidate with the highest RP5H.

## Data quality

The candidate universe is the exact intersection of IDs in `data/models.json`
and `data/go.json`, minus reviewed excludes in `config/model-decisions.json`.
One-sided models are reported as catalog exclusions.
Missing `rp5h` or an Arena score excludes a candidate from target selection.
Missing `usage_quota` or price data is reported for catalog review but does
not affect mapping eligibility. Missing RP5H requires an exclude/supplement
decision. Arena direct matches are high confidence;
contributor-suffix and version-downgrade matches are medium confidence;
prefix matches and free defaults are low confidence. Unmatched request
models are blocking errors for an apply workflow.
