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
metadata, not mapping-score dimensions; they are written to AxonHub through
the catalog plan.

## Baseline routing

Only `claude-*` request models participate in the mapping (GPT requests are
pass-through, ADR 0012). Within the claude series, the request model with the
lowest valid Arena score is the baseline. The baseline bypasses the formula
and maps to the eligible free candidate with the highest RP5H. If no free
candidate is available, it maps to the eligible candidate with the highest
RP5H.

## Data quality

The candidate universe is the set of model ids in `data/models_extra.json`
after cross-channel dedupe — each id belongs to the channel with the highest
`rp5h` (null loses to a value, ties keep the alphabetically first channel) —
minus reviewed excludes in `config/model-decisions.json`. Channel-provided
`claude-*` models are never collected, so they cannot become candidates. A
free candidate has its `rp5h` re-derived from its owning channel's largest
non-free `rp5h`; missing `usage_quota` becomes 60.
Candidates still missing `rp5h` or an Arena score are ineligible for target
selection and reported with reasons.
Missing `usage_quota` or price data is reported for catalog review but does
not affect mapping eligibility. Arena direct matches are high confidence;
contributor-suffix and version-downgrade matches are medium confidence;
prefix matches and free defaults are low confidence. Unmatched request
models are blocking errors for an apply workflow.
