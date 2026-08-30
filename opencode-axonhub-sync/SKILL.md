---
name: opencode-axonhub-sync
description: Refresh the OpenCode Go cache and reconcile its three managed AxonHub channels, model cards, structured remarks, and candidate channel associations. Use for OpenCode/AxonHub catalog synchronization, not Claude/GPT request mappings or unrelated channels.
---

# OpenCode → AxonHub catalog sync

Manage only `opencode-go`, `op-responses`, `op-anthropic`, and the OpenCode
models they expose. `models-mapping` owns Claude/GPT associations and profile
templates. Channels outside the configured scope are read only.

Read [references/schema.md](references/schema.md) when changing source shapes,
remark fields, association preservation, or deletion safety. Use
`scripts/sync_models.py` for planning, applying, and verification.

## Plan

Run from the repository root as the AxonHub service user. Refresh the provider
and require a non-empty cache:

```bash
opencode models opencode-go --refresh --verbose
test -s "$HOME/.cache/opencode/models.json"
```

Generate a read-only plan:

```bash
python3 opencode-axonhub-sync/scripts/sync_models.py \
  --provider opencode-go \
  --cache "$HOME/.cache/opencode/models.json" \
  --go data/go.json \
  --model-decisions config/model-decisions.json \
  --snapshot-output data/models.json \
  --axonhub-url "$AXONHUB_URL" \
  --plan-output /tmp/opencode-axonhub-sync-plan.json
```

Review `included`, `excluded`, `decisionRequired`, channel before/after lists,
candidate association diffs, `externallyRetained`, `blockingReferences`, and
creates/updates/deletes. Source/model/channel fingerprints bind the plan to the
reviewed state. Errors, unresolved decisions, or references to a delete target
block apply.

## Apply

Apply only after the user explicitly confirms this catalog plan:

```bash
python3 opencode-axonhub-sync/scripts/sync_models.py \
  --provider opencode-go \
  --plan-input /tmp/opencode-axonhub-sync-plan.json \
  --cache "$HOME/.cache/opencode/models.json" \
  --go data/go.json \
  --model-decisions config/model-decisions.json \
  --snapshot-output data/models.json \
  --axonhub-url "$AXONHUB_URL" \
  --apply --verify
```

Use `AXONHUB_JWT` when present. Otherwise the helper may mint a short-lived JWT
from `$HOME/.config/axonhub/axonhub.db` read-only. Never print or persist either
secret. The verified snapshot replaces `data/models.json` only after apply and
admin verification succeed; inspect and push it separately. The helper never
runs Git commands.

Verify `/v1/models` separately with `AXONHUB_API_KEY`, checking HTTP 200, model
count, and at least one model from each changed managed channel.

## Reconciliation contract

- For `opencode-go`, included models are the exact cache/go.mdx intersection
  minus reviewed `exclude` decisions. Other providers use their cache node.
- Map go protocols as `completions → opencode-go`, `responses → op-responses`,
  `messages → op-anthropic`. A model-level `api.npm` conflict is blocking.
- Missing protocol is blocking. Missing RP5H is decision-required unless an
  exclude/supplement decision resolves it. Optional remark metadata may be
  `null`.
- Each managed channel's `supportedModels` becomes its exact included protocol
  partition. Credentials, tags, quotas, ordering, and unmanaged channels stay
  unchanged.
- Each included candidate keeps every unmanaged association and receives
  exactly one enabled `channel_model` association to its protocol-derived
  managed channel. Old managed-channel associations are replaced.
- Cache/go one-sided models enter `excluded`. Remove them only from managed
  channels. A model used by an unmanaged channel enters `externallyRetained`.
  Exact association references block deletion; only unreferenced, externally
  unused model objects enter confirmed `deletes`.
- Existing model status is preserved. New included models are created enabled.
  Stale models are never inferred from version numbers.
- Model cards come from the cache, including `cache_read/cache_write` cost.
  Remarks are canonical JSON containing `manual`, `rp5h`, `usage_quota`,
  `context_threshold`, `peak_hours`, and `retention`; preserve `manual`.
- Equality checks precede every mutation. An unchanged second plan has no
  creates, updates, channel updates, or deletes.

Catalog confirmation never authorizes the separate mapping plan.
