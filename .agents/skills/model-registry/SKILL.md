---
name: model-registry
description: Offline planner that turns watch-pipeline snapshots into a deduplicated model registry, Claude mapping suggestions (models.csv), and two per-object AxonHub plans (data/channel-plan.json allowlists, incremental data/model-plan.json). Dedupes models_extra across channels by highest rp5h, references cards from models.dev, scores Claude mappings by Arena formula. Read-only planner; execution belongs to axonhub-admin.
---

# Model Registry

Turn the watch-pipeline snapshots into one deduplicated registry and the two
reviewable artifacts the AxonHub write path consumes. Division of labor
(ADR 0012): CI collects data (`watch-pipeline.yml`), this skill computes, and
`axonhub-admin` writes. This skill never mutates AxonHub and holds no
credentials.

Inputs (all committed snapshots, fully offline):

- `data/models_extra.json` — channel-declared facts per `channels.<channel>.<model_id>`:
  `rp5h`, `usage_quota`, `cost{}`, a hand-maintained `free: true`, and
  `tok_s` (goat only); a hand-maintained `exclude` reason on a record keeps
  the model out of the registry.
- `data/all_models.json` — models.dev flat `vendor/model` catalog; the only
  card source (never a list source).
- `data/arena.json` — Arena scores feeding the mapping formula.
- `models.csv` — the mapping workspace; its `role=request` rows are the
  hand-maintained request-model list and the only cells read back (GPT rows
  are pass-through and reported as ignored).

## Plan

```bash
python3 .agents/skills/model-registry/scripts/models_mapping.py \
  --csv models.csv \
  --fail-on-errors
```

Writes `models.csv` plus two per-object plans under `data/` (ADR 0017):
`channel-plan.json` (each channel's authoritative `supportedModels`
desired state — the manual replacement for AxonHub's autoSync) and
`model-plan.json` (incremental model entries: `cardRef` into
`all_models.json`, merged cost end values, remarks, derived meta,
`channelPriority` chains). Warnings travel with the artifact they
explain; arena/mapping warnings stay on stderr. Render one model's full
AxonHub input at write time with:

```bash
python3 .agents/skills/model-registry/scripts/assemble_card.py --id <modelID>
```

The rules per AxonHub object live in `reference/` —
[channel.md](reference/channel.md) (channel allowlists: dedupe, variant
governance, free completion), [models.md](reference/models.md) (cards and
remark), [associations.md](reference/associations.md) (the Claude mapping
formula and the non-Claude routing conventions); numeric thresholds are
constants in the scripts — the reference explains them, the code decides.
Pipeline, in order:

1. **Alias normalisation** — the hand-maintained `aliases` map merges
   cross-channel spellings; channel lists keep native ids, the registry and
   `plan.models[]` use the canonical id with `channelAliases` for routing.
2. **Excluded records** — speed-marketing ids (`-fast`/`-highspeed`) and
   records with a hand-maintained `exclude` reason are skipped with a warning.
3. **Dedupe** — a model id listed by several channels belongs to the channel
   with the highest `rp5h` (ties keep the alphabetically first channel);
   every resolution reports `duplicate_model_across_sources`.
4. **Variant grouping** — within a base model, `-free` beats `-contributor`
   beats the plain original; superseded variants leave with
   `variant_superseded`.
5. **Free completion** — a free model's `rp5h` is re-derived from its channel's
   largest non-free `rp5h`; a missing `usage_quota` becomes 60.
6. **Cards** — filled from `data/all_models.json` only; channel `cost` wins
   field-by-field; no card means channel-claimed data plus `card_missing`.
7. **Arena + rp5h triage** — only direct and same-model variant-suffix
   matches score (`arena_borrowed_rejected` otherwise); no Arena match
   leaves a non-free model unscored (`arena_missing`), free models default
   to 1500 (`arena_defaulted`); a missing `rp5h` excludes below an Arena
   score of 1500 (`rp5h_missing_excluded`) and keeps with a review warning
   at or above it (`rp5h_missing_review`, target-selection ineligible
   only).
8. **Claude mapping** — the formula scores every request over the scored
   non-free candidates, then free fill pairs the ascending free pool with
   the ascending requests (`free_fill`). No overrides; weights and order in
   [reference/associations.md](reference/associations.md).
9. **Outputs** — `models.csv` regenerated in place (request rows kept as
   the input list, every other cell recomputed) plus, under `data/`:
   `channel-plan.json` (per-channel exact `supportedModels` with native
   ids keyed by section name) and `model-plan.json` (incremental entries —
   `cardRef`, cost end values, remarks, derived meta, `channelAliases`,
   `channelPriority` chains by descending rp5h); `assemble_card.py`
   renders the full AxonHub input at write time.

Missing request Arena evidence or source schema drift are blocking errors;
with `--fail-on-errors` the run exits non-zero and the artifacts are for
inspection only. Everything else (remark gaps, arena fallbacks, card
misses) is a warning.

## Handoff

- **Model cards + channel `supportedModels`** — hand the plan to
  `axonhub-admin`'s interactive procedure; the user confirms before anything
  is written.
- **Claude mappings** — the user reads `models.csv` and then chooses: agent
  writes via `axonhub-admin`, or manual edit in the AxonHub UI. Day-to-day
  retargeting happens directly in the AxonHub UI without this pipeline.
- **Claude global models** — created once by hand in AxonHub; never scripted.
