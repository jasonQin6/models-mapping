---
status: accepted
---
# go.mdx owns the opencode-go model list; models.dev only fills cards

The old pipeline took the models.dev `opencode-go` provider (a mirror of the
channel's advertised `/v1/models`) as the model list and used go.mdx only to
append quotas. The advertised list is a superset of the actual entitlement:
seven models (`glm-5`, `grok-4.5`, `kimi-k2.5`, `qwen3.5-plus`, `mimo-v2-pro`,
`mimo-v2-omni`, `hy3-preview`) entered the catalog without a go.mdx row, so
they had no `rp5h` and mapping scoring could not use them. This supersedes the
sentence in ADR 0005 that derived the opencode-go managed catalog from a
cache/go intersection.

Decision: a go.mdx row **is** the opencode-go list. `watch_go.py` builds the
snapshot from go.mdx ids only (models.dev-only ids are dropped); models.dev is
demoted to a card-data filler for the union catalog (go ∪ goat) via
`sync_models.py --fill-source`, where channel snapshot values always win and
every fill is reported as an auditable `models_dev_filled` warning.
Go-exclusive ids (in go.mdx, absent from models.dev) keep go-declared fields
only — the ADR 0010 GOAT-exclusive precedent.

## Considered options

- **Keep models.dev as the list**: rejected — the advertised list is not the
  entitlement; unquota-ed models pollute the candidate universe.
- **Union of go.mdx and models.dev ids**: rejected — puts undocumented models
  back into the catalog with no entitlement evidence.
- **Copy goat quotas onto go models missing rp5h**: rejected — the two
  channels' rp5h/usage_quota values differ (deepseek-v4-flash: go 7600/30,
  goat 18200/60) and must never be copied across channels.

## Consequences

- The seven models leave the go snapshot and the candidate universe until
  go.mdx documents them; `ox-alpha-free` (models.dev-only, retired upstream)
  disappears as well.
- Each parsed record is written with both top-level quota fields (consumed by
  `build_mapping.py`) and a mirrored `extra` object (consumed by
  `sync_models.py`) from the same parse — no drift by construction.
- `fetch-opencode-go` loses its `changed` gate (go.mdx cadence is independent
  of models.dev) and gains a `--models-dev` card-fill input.
- Models.dev drift against the channel surfaces only as change-report signal
  (added/removed in `all_models.json`), never as catalog membership.
