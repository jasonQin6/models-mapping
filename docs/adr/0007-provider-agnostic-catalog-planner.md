---
status: accepted
---

# Merged mapping skill with provider-agnostic catalog planning and free-model enrichment

`opencode-axonhub-sync` coupled one data source (the OpenCode cache), one
protocol→channel mapping, and AxonHub execution into a single helper. Its
planning half merged into `models-mapping` (scripts `sync_models.py`), and its
execution half moved to `axonhub-admin/scripts/apply_catalog_plan.py`.

`models-mapping` now owns one enrichment record and two plan types over the
watch-pipeline snapshots:

- **Candidate universe**: the union of `data/opencode-go-models.json` and
  `data/goat-models.json`, minus request models and reviewed excludes. This
  supersedes the ADR 0006 rule that GOAT stays outside the mapping
  intersection. Duplicates across sources keep the first CLI-ordered source
  and emit a warning.
- **Free-model enrichment** (`data/enriched.json`): a free model missing
  `rp5h` copies its own channel's largest non-free `rp5h`; missing
  `usage_quota` becomes 60. Channels are enriched independently — never
  cross-channel. The record stores only derived fields plus provenance.
- **Candidates missing `rp5h` or an Arena score are ineligible** for target
  selection and reported with reasons; family score inheritance was
  considered and rejected.
- **Catalog plans**: any provider→channel pair via `--source` (repeatable)
  and `--provider-channel`. The `commandcode` channel is append-only — its
  `supportedModels` keeps vendor-prefixed upstream entries and gains bare IDs
  as they arrive; `opencode-go` is replaced with the exact included list.
  Remark fields read from `extra` then top level; missing fields are warnings.
- **Change report**: `data/all_models.json` diff (added/removed models) and
  snapshot `cost` diffs (price changes) against `HEAD~1`. Trigger gating is
  watch-pipeline's dependency chain, not the planner.
- **Mapping suggestions** stay in `models.csv` via the existing scoring
  formula; request-side Arena scores still come from `data/arena.json`
  matching (`arena_model_id` alias). `config/request-models.json` gains no
  score field.

Execution of both plan types belongs to `axonhub-admin`: it validates plan
shape, re-checks source fingerprints, re-reads remote before-state for drift,
applies mutations after explicit user confirmation of the plan file, and
reads back every written value. JWT minting and credential policy exist only
in `axonhub-admin`; planners require `AXONHUB_JWT` (or `--token`) for
read-only state queries and embed no secrets in plans.

## Consequences

- The `data/models.json` normalized snapshot, `data/go.json`, the OpenCode
  cache input, and the `completions/responses/messages` channel partition are
  retired; `data/enriched.json` is the only new derived artifact.
- `data/formula.md`'s candidate-universe definition follows this ADR (union,
  not intersection).
- A plan file is the only interface between planning and execution; a stale
  or edited plan is refused at apply time.
