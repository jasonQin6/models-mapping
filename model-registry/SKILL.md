---
name: model-registry
description: Offline planner that turns watch-pipeline snapshots into a deduplicated model registry, Claude mapping suggestions (models.csv), and a schema-2 catalog plan for AxonHub. Dedupes models_extra across channels by highest rp5h, fills cards from models.dev, scores Claude mappings by Arena formula. Read-only planner; execution belongs to axonhub-admin.
---

# Model Registry

Turn the watch-pipeline snapshots into one deduplicated registry and the two
reviewable artifacts the AxonHub write path consumes. Division of labor
(ADR 0012): CI collects data (`watch-pipeline.yml`), this skill computes, and
`axonhub-admin` writes. This skill never mutates AxonHub and holds no
credentials.

Inputs (all committed snapshots, fully offline):

- `data/models_extra.json` — channel-declared facts per `channels.<channel>.<model_id>`:
  `rp5h`, `usage_quota`, `cost{}`, `tok_s` (goat only), `context_threshold`,
  `peak_hours`, `retention`.
- `data/all_models.json` — models.dev flat `vendor/model` catalog; the only
  card source (never a list source).
- `data/arena.json` — Arena scores feeding the mapping formula.
- `config/request-models.json` — fixed request models; only `claude-*` rows
  enter the mapping (GPT is pass-through).
- `config/model-decisions.json` — `scope.channels` (provider→channel),
  per-model exclude/supplement, `mapping_overrides`.

## Plan

```bash
python3 model-registry/scripts/models_mapping.py \
  --csv-output models.csv \
  --plan-output /tmp/catalog-plan.json \
  --fail-on-errors
```

Pipeline, in order:

1. **Alias normalisation** — the hand-maintained `aliases` map in
   `data/models_extra.json` (e.g. `tencent-hy3` -> `hy3`) merges
   cross-channel spellings. Channel lists keep native ids; the registry and
   `plan.models[]` use the canonical id, with `channelAliases` describing
   per-channel exposure for routing.
2. **Collector excludes** — records stamped `exclude`
   (`-fast`/`-highspeed` speed variants) are skipped with a warning.
3. **Dedupe** — a model id listed by several channels belongs to the channel
   with the highest `rp5h` (null loses to a value, ties keep the
   alphabetically first channel); every resolution reports
   `duplicate_model_across_sources`.
4. **Variant grouping** — within a base model, `-free` beats `-contributor`
   beats the plain original; superseded variants leave with
   `variant_superseded`.
5. **Free fill** — per owning channel, a free model's `rp5h` is re-derived
   from the channel's largest non-free `rp5h` and a missing `usage_quota`
   becomes 60 (`free_default_filled`).
6. **Cards** — filled from `data/all_models.json` only; channel `cost` wins
   field-by-field. A model with no card keeps channel-claimed data and
   reports `card_missing` (no data is invented).
7. **Arena + rp5h triage** — a non-free model without an Arena match gets a
   default score of 1500 (`arena_defaulted`) so beta models stay in the
   pool; a non-free model missing `rp5h` is excluded below an Arena score of
   1500 (`rp5h_missing_excluded`) and kept with a review warning at or above
   it (`rp5h_missing_review`, ineligible for target selection only).
8. **Claude mapping** — baseline routing (lowest-arena request of the claude
   series maps to the free candidate with the highest `rp5h`) plus the
   Arena/RP5H/proximity formula (weights in `data/formula.md`); request
   models may pin `arena_score` in `config/request-models.json`;
   `mapping_overrides` win last.
9. **Outputs** — `models.csv` (reviewable: candidates for context, one
   `mapping` per `claude-*` request row) and the schema-2 plan (per-channel
   exact `supportedModels` with native ids, canonical model cards with
   `channelAliases`, removals, warnings).

Missing request Arena evidence, unknown override targets, or source schema
drift are blocking errors; with `--fail-on-errors` the run exits non-zero and
the artifacts are for inspection only. Everything else (remark gaps,
arena fallbacks, card misses) is a warning.

## Handoff

- **Model cards + channel `supportedModels`** — hand the plan to
  `axonhub-admin`'s interactive procedure; the user confirms before anything
  is written.
- **Claude mappings** — the user reads `models.csv` and then chooses: agent
  writes via `axonhub-admin`, or manual edit in the AxonHub UI. Day-to-day
  retargeting happens directly in the AxonHub UI without this pipeline.
- **Claude global models** — created once by hand in AxonHub; never scripted.
