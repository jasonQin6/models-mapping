# Scoring Formula

```
match = 0.30*score + 0.25*rp5h + 0.15*usage_quota + 0.30*proximity - penalty + upgrade_bonus

score         = cand_score / max_score
rp5h          = log(cand_rp5h + 1) / log(max_rp5h + 1)
usage_quota   = cand_usage_quota / max_usage_quota
proximity     = 1 - |cand_score - src_score| / max_score_diff
penalty       = 0.2 * (src_score - cand_score) / max_score_diff  (only when cand < src)
upgrade_bonus = 0.1  (only when cand_score > src_score)
```

## Weight rationale

- **score (0.30)**: Arena quality rating
- **rp5h (0.25)**: Request capacity (log-compressed to prevent domination by high-quota models)
- **usage_quota (0.15)**: Dollar quota capacity
- **proximity (0.30)**: Score closeness — drives diversity by preferring candidates near the source model's score
- **penalty (0.2)**: Asymmetric downgrade penalty — high→low mapping discouraged
- **upgrade_bonus (0.1)**: Binary bonus for upgrades — encourages high-score replacements

## Cheap model routing

Cheap models skip scoring:
- **claude-haiku** → free opencode model (id contains "free"), or highest RP5H if none
- **gpt-5.4-mini** → highest RP5H model

Free model compatibility: if an opencode model's id contains "free" and its RP* values are 0, fetch_data copies them from the highest RP5H model.

## Tuning notes

Weights are in `scripts/compute_mapping.py`. Changes need re-validation against real data.
