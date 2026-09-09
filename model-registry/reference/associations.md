# Model associations

The third AxonHub object this project governs: a model's
`settings.associations` — the rules deciding which channel entry serves
each request. Two regimes, split by series. **Claude request models** are
mapped by score to other existing models; the planner computes them into
`models.csv` for review. **Every other global model** maps to itself; the
work is channel selection, priority, and merging free variants, and the
conventions are hand-authored in AxonHub (UI or axonhub-admin) — this file
is the shared rulebook, not planner output.

## Claude request models — score-based mapping

`models.csv` (the mapping workspace, see CONTEXT.md) is the review
artifact: its `role=request` rows are the hand-maintained request-model
list, every other cell is recomputed each run. Only `claude-*` requests
participate; GPT requests pass through by name and never enter the mapping
(ADR 0012).

For each request, pick the non-free candidate with the highest score:

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

`proximity` is `1` when every candidate has the same score. The penalty
applies only when the candidate is below the request's score, the upgrade
bonus only when above. Prices and `usage_quota` are source metadata, not
mapping-score dimensions; they reach AxonHub through the catalog plan
(see [models.md](models.md)).

Mapping order:

1. **Formula first** — every request is scored against the non-free
   candidates with the formula above.
2. **Free fill** — the free pool sorted by Arena score ascending pairs
   with the requests sorted by Arena score ascending, replacing those
   requests' formula targets (`free_fill`): the lowest-quality request
   gets the lowest-scored free model.

There are no overrides: the computed pairings are the review suggestion,
and day-to-day retargeting happens in the AxonHub UI after the review
confirms the table. The executed shape is exactly one enabled `type=model`
association per request model targeting the confirmed candidate — the one
case where associations are wholesale-replaced (axonhub-admin writes it
for the fixed request models only).

### Arena score sources

Scores come from the leaderboard, hand assignments (`manual: true`
records in `data/arena.json`, preserved across collection runs), or the
defaults below. The matching chain strips a fixed set of variant suffixes
(`-contributor`, `-free`, `-vl` — `MATCH_VARIANT_SUFFIXES` in
`model-registry/scripts/name_matching.py`): a variant the board does not
list inherits
its base model's score (`ling-3.0-flash-vl` <- `ling-3.0-flash`), so one
hand-assigned base record covers every variant. Arena ids keep the
board's parameter-size suffixes verbatim (`qwen3.8-27b`), so channel ids
carrying the size direct-match.

- A non-free model with no Arena match defaults to 1500 (warning,
  `arena_defaulted`) so beta models stay in the pool.
- A free model whose stripped base is also absent defaults to 1500 too
  and stays in the pool, reachable through free fill regardless of score.
- A request missing from the board is a blocking error
  (`request_arena_missing`) until a hand assignment lands.

Confidence: Arena direct matches are high; variant-suffix and
version-downgrade matches medium; prefix matches and free defaults low.
An unmatched model carrying an alphabetic tail outside the registry (say
`-vq`) raises `unrecognized_variant_suffix` — it is never silently
scored. Human triage picks one: hand-assign an Arena record for the full
id, merge the spelling via `aliases` in `data/models_extra.json`
(see [channel.md](channel.md)), or, once the suffix proves to be a
recurring family marker, register it in `MATCH_VARIANT_SUFFIXES`.
Versioned tails (`-a55b`, `-0902`) are never flagged.

## Non-Claude models — self-mapping conventions

A non-Claude global model routes to itself: its associations bind the
bare-ID model to the channel entries that serve it. Premise: upstream
providers expose vendor-prefixed ids (`deepseek/deepseek-v4-flash`) while
AxonHub Model entities use bare ids, and associations match exact
strings. The two fixes — channel-side `settings.modelMappings` and
model-side `regex` associations — are axonhub-admin's model-ID gotcha;
association types: `channel_model` (pinned channel + exact id), `model`
(exact id across all channels), `regex` (global pattern).

- **Channel selection** — one channel serves the id: a `channel_model`
  pin (plus that channel's `modelMappings` making the bare id routable).
  Several channels serve it: one global `regex` at p0 matching the bare id
  everywhere (`(?i)(^|/)deepseek-v4-flash$`) — the default; locking a
  model to specific channels is the exception.
- **Priority configuration** — ascending priority numbers are the
  fallback order: p0 primary, p1/p2 fallbacks. A fallback chain is same
  `channel_model` rules on one channel with ascending priorities.
- **Free-variant merging** — a free family merges under one global model:
  the primary rule targets the base variant and the sibling free variants
  ride as in-channel fallbacks — `ling-3.0-flash` → ant's `vl`/`sante`/
  `fin` at p0/p1/p2.
- **Strict fallback demotion** — the main rule keeps p0 with
  `exclude: [{channelIds: [<fallback channel>]}]` and the fallback
  channel gets a p1 `channel_model` rule: daily traffic never touches it,
  429s and failures do.
- **Time-gated routing** — a p0 rule on the free channel wrapped in a
  `when` daily_time group, the unrestricted main pool demoted to p1
  (input shape in axonhub-admin's deployment quirks).

Writes go through axonhub-admin's interactive program; every pattern is
verified with `queryModelChannelConnections(associations: $assocs)` —
done when the target channel resolves the expected `actualModel` with
`source: mapping` or `direct`.
