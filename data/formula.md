# Mapping scoring formula

`models.csv` is the mapping review artifact. Its `role=request` rows are the
hand-maintained request-model list (the source of truth for which Claude
requests are mapped); every other cell — candidate rows, Arena scores,
RP5H, the one-to-one `mapping` column — is regenerated deterministically
from the snapshots on each run.

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

## Mapping order

Only `claude-*` request models participate in the mapping (GPT requests are
pass-through, ADR 0012). Two steps, in order:

1. **Formula first** — every request is scored against the non-free
   candidates with the formula above.
2. **Free fill** — the free pool sorted by Arena score ascending is paired
   with the requests sorted by Arena score ascending, replacing those
   requests' formula targets (`free_fill`): the lowest-quality request gets
   the lowest-scored free model.

There are no mapping overrides: the computed pairings are the review
suggestion, and day-to-day retargeting happens in the AxonHub UI after the
review confirms the table.

## Data quality

The candidate universe starts from every record in `data/models_extra.json`
(channel-provided `claude-*` models are never collected) and is reduced by:

- **Alias normalisation** — the hand-maintained `aliases` map in
  `data/models_extra.json` merges cross-channel spellings (`tencent-hy3` ->
  `hy3`); channel lists keep the native id, the registry uses the canonical
  one, and `plan.models[].channelAliases` describes the exposure.
- **Speed-marketing variants** — ids ending `-fast`/`-highspeed` are
  derived as excluded at planning time; a record carrying a hand-maintained
  `exclude` reason in `data/models_extra.json` likewise never enters the
  registry.
- **Cross-channel dedupe** — one winning channel per id: highest `rp5h`
  (null loses to a value, ties keep the alphabetically first channel).
- **Variant grouping** — within a base model, `-free` beats
  `-contributor` beats the plain original; superseded variants leave with a
  warning.
- **rp5h triage** — a non-free model missing `rp5h` is excluded when its
  Arena score is below 1500; at or above 1500 it stays in the channel list
  with a review warning and is only ineligible for target selection.
- **Manual excludes** — `exclude` reasons on records in
  `data/models_extra.json` (field-level merges preserve them across
  collection runs).

Scores come from the leaderboard, hand assignments (`manual: true` records
in `data/arena.json`, preserved across collection runs), or the defaults
below. A non-free model with no Arena match gets a default score of 1500
(warning)
so beta models stay in the pool. A free model inherits its base variant's
Arena score when the board lists it (`longcat-2.0-free` <- `longcat-2.0`,
warning `arena_inherited`) and defaults to 1500 when it does not; free
models are always reachable through baseline routing regardless of score. A
free candidate has its `rp5h` re-derived from its owning channel's largest
non-free `rp5h`; missing `usage_quota` becomes 60. Arena ids keep the
board's parameter-size suffixes verbatim (`qwen3.8-27b`), so channel ids
carrying the size direct-match. Request models come from the `role=request`
rows of `models.csv`; their Arena scores are recomputed from the snapshot
each run, and a request missing from the board gets a hand assignment
(`manual: true` record in `data/arena.json`).
Arena direct matches are high confidence; contributor-suffix and
version-downgrade matches are medium confidence; prefix matches and free
defaults are low confidence. Unmatched request models are blocking errors
for an apply workflow.
