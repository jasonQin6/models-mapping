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
  structured remarks, and `channel_model` associations for the
  `opencode-go`/`commandcode` channels (any provider→channel pair works).
- **Enrichment record** — `data/enriched.json`: derived-only fields
  (free-model fills) with provenance.

Execution of both plan types belongs to `axonhub-admin`
(`apply_mapping.py`, `apply_catalog_plan.py`). This skill never mutates
AxonHub. Channels other than the two planned are read only.

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
AXONHUB_JWT=<jwt> python3 models-mapping/scripts/sync_models.py \
  --source data/opencode-go-models.json \
  --source data/goat-models.json \
  --provider-channel commandcode-goat=commandcode \
  --model-decisions config/model-decisions.json \
  --change-report-output /tmp/change-report.json \
  --plan-output /tmp/catalog-plan.json
```

Each `--source` contributes its provider; `--provider-channel` routes a
provider to a channel (default: provider id). `commandcode` is append-only:
its `supportedModels` keeps existing vendor-prefixed entries and only gains
missing bare IDs. `opencode-go` is replaced wholesale with the exact included
list. The change report diffs `data/all_models.json` (added/removed models)
and both snapshots (price changes) against `HEAD~1`. Missing remark fields
(`rp5h`, `usage_quota`, `context_threshold`, `peak_hours`, `retention`) are
warnings, not blockers.

Review `included`, `excluded`, channel before/after, association diffs,
`externallyRetained`, `blockingReferences`, creates/updates/deletes, and the
change report. Done when: the plan file is written with zero errors and the
user has reviewed it. Hand the plan to `axonhub-admin` for confirmation and
execution. Confirming the catalog plan never authorizes the mapping plan.
