---
name: models-mapping
description: Enrich and reconcile watch_* snapshots (data/*.json) into mapping suggestions and catalog plans for AxonHub. Fills free-model quotas, reports model/price changes, suggests Claude/GPT mappings via models.csv, and produces channel/model-card catalog plans. Read-only planner; execution belongs to axonhub-admin.
---

# Models Mapping

Turn the watch-pipeline snapshots into reviewable plans. One skill, two plan
types, one enrichment record:

- **Mapping suggestions** — `models.csv`: one canonical target per fixed
  Claude/GPT request model (scoring formula in `data/formula.md`).
- **Catalog plans** — plan JSON: channel `supportedModels`, model cards,
  structured remarks, and `channel_model` associations for the managed
  channels — the provider→channel scope in `config/model-decisions.json`.
- **Enrichment record** — `data/enriched.json`: derived-only fields
  (free-model fills) with provenance.

Execution of both plan types belongs to `axonhub-admin`'s interactive agent
procedure (ADR 0009). This skill never mutates
AxonHub. Channels outside the managed scope are read only.

## Rebuild, enrich, and validate

From the repository root:

```bash
review_dir="$(mktemp -d)"
python3 models-mapping/scripts/build_mapping.py \
  --model-decisions config/model-decisions.json \
  --output "$review_dir/models.csv" \
  --enriched-output data/enriched.json \
  --report-output "$review_dir/report.json" \
  --fail-on-errors
cmp models.csv "$review_dir/models.csv"
```

The candidate universe is the union of `data/opencode-go-models.json` and
`data/goat-models.json` (duplicates keep the first source listed in the CLI
order, warning attached), minus request models and reviewed excludes. Free
models missing `rp5h` copy their own channel's largest non-free `rp5h`;
missing `usage_quota` becomes 60 — recorded in `data/enriched.json` with the
source channel. Candidates still missing `rp5h` or an Arena score are listed
in `report.ineligible` with reasons and never enter target selection.

The CSV must have exactly `model_id,role,arena_score,rp5h,mapping`. Missing
request Arena evidence, unresolved decisions, unknown targets, or source
schema drift are blocking.

## Catalog plan

```bash
python3 models-mapping/scripts/sync_models.py \
  --source data/opencode-go-models.json \
  --source data/goat-models.json \
  --model-decisions config/model-decisions.json \
  --change-report-output /tmp/change-report.json \
  --plan-output /tmp/catalog-plan.json
```

Planning is fully offline — snapshots plus config, no JWT, no network. Each
`--source` contributes its provider; routing defaults to the managed
provider→channel scope in `config/model-decisions.json`, and a source provider
or `--provider-channel` pair outside that scope is rejected. The plan is pure
desired state: per channel an exact bare-ID `supportedModels` list applied
wholesale at execution, target model-card values for every included model, and
removal candidates annotated for the execution-time external-reference check.
It has no modes, fingerprints, or remote before-state — regenerate it rather
than staleness-checking it, and preserve the remote remark `manual` field when
applying. The change report diffs `data/all_models.json` (added/removed
models) and both snapshots (price changes) against `HEAD~1`. Missing remark
fields (`rp5h`, `usage_quota`, `context_threshold`, `peak_hours`, `retention`)
are warnings, not blockers.

Review `providers`, per-channel `supportedModels`, the model targets,
`removals`, and the change report. Done when: the plan file is written and the
user has reviewed it. Hand the plan to `axonhub-admin` for interactive
confirmation and execution. Confirming the catalog plan never authorizes the
mapping plan.
