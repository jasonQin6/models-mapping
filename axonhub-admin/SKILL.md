---
name: axonhub-admin
description: Operate AxonHub (AI gateway) over its admin GraphQL API — interactively execute confirmed catalog plans and mapping tables (channel supportedModels, model cards, removals, request-model routing), view/change channels and models, manage API-key templates, and tune channel tags, weights, and non-fixed-model routing. Use when the user asks to view or change anything in AxonHub without the web UI.
---

# AxonHub Admin

Operate AxonHub over HTTP as an agent. All management operations live on one endpoint, `/admin/graphql`, and authenticate with a **JWT token** — never a static key. The token comes from the user's logged-in AxonHub browser tab.

## Step 1 — Obtain the token

The convention: the live JWT is read from the user's logged-in AxonHub browser tab and exported as `AXONHUB_JWT` for the rest of the run.

1. Check `AXONHUB_JWT` in the environment — if non-empty, use it (a token is valid for 7 days from sign-in).
2. Otherwise claim the user's AxonHub browser tab and read the token from page context: `localStorage.getItem('axonhub_access_token')`, then export it as `AXONHUB_JWT`.
3. If no logged-in tab exists, ask the user to sign in at the server (or supply credentials for `POST /admin/auth/signin` with `{email, password}` — the response contains the token). Then export it as `AXONHUB_JWT`.

Verify before proceeding:

```bash
curl -sS -X POST "${AXONHUB_URL:-https://axon.jasonqin.site}/admin/graphql" \
  -H "Authorization: Bearer $AXONHUB_JWT" \
  -H "Content-Type: application/json" \
  -d '{"query": "{ me { id email } }"}'
```

Done when: HTTP 200 with `data.me`. A 401 means the token expired or was cleared — fall back to step 3 of the list above instead of retrying.

## Step 2 — The interactive write program (ADR 0009)

Every AxonHub write happens in an interactive session by following this program. There are no plan-gated apply scripts: the guardrails are this document, and the drift check is live state re-read before every write.

### The four guardrails (hard, non-negotiable)

1. **Touch managed channels only.** The managed set is the provider→channel scope in `config/model-decisions.json` (`scope.channels`) plus the managed templates (`scope.templates`). Nothing outside it is created, updated, or deleted — even when the GraphQL response makes it easy.
2. **Preserve unmanaged associations and external references.** Association lists are replaced per model: keep every rule that does not belong to this write. A model object referenced by an external (non-managed) channel's `supportedModels`, or by any association of another model, is never deleted — retain it and report.
3. **Read before write.** Fetch the live object immediately before each mutation. Never write from the plan alone or from a cached read; live state is the only write basis.
4. **Write then verify, no speculative retries.** Read back every write and compare field by field. A mismatch or a failed item is reported as-is — never retried blindly, never rolled back speculatively.

### Confirmation (AskUserQuestion)

Two independent confirmations; confirming one never authorizes the other:

- **Catalog plan** — the offline plan JSON from `models-mapping/scripts/sync_models.py` (schema 2, desired state): per-channel exact bare-ID `supportedModels`, target model-card values for every included model, `removals` candidates, `warnings`.
- **Mapping table** — `models.csv` (the only mapping review artifact): `request → mapping` rows for the fixed request models.

For each confirmation, show the artifact next to live state (current channel lists, current request-model targets) so the diff is visible, then ask. Execute only the confirmed set; anything the user declines is skipped and reported.

### Read remote state (before any write)

Fetch channels, models, and API-key profile templates in full. Note: this
deployment's server errors on a channels query combining `tags` with
`settings` — fetch them in two queries and join by `id`:

- Channels: `channels(first: 50) { edges { node { id name type status supportedModels manualModels autoSyncSupportedModels baseURL orderingWeight tags } } } }` and `channels(first: 50) { edges { node { id settings { modelMappings { from to } } } } }`
- Models: `models(first: 100) { edges { node { id modelID name developer status remark modelCard { reasoning { supported default } toolCall temperature modalities { input output } vision cost { input output cacheRead cacheWrite } limit { context output } knowledge releaseDate lastUpdated } settings { disableDeveloperSettingsInheritance loadBalancerStrategy traceStickyMode associations { type priority disabled channelModel { channelId modelId } modelId { modelId } regex { pattern } } } } } } }`
- API-key profile templates: `apiKeyProfileTemplates { id name linkedProfilesCount profile { name modelMappings { from to } channelIDs channelTags channelTagsMatchMode modelIDs loadBalanceStrategy traceStickyMode quota { … } } }`

Done when: you can name each managed channel's ID, its exact `supportedModels`, and each affected model's `modelID`, status, and current associations.

### Execution-time check: autoSyncSupportedModels must be off

AxonHub's hourly upstream sync overwrites a channel's `supportedModels` with `manualModels ∪ 上游全量`, which erases the curated catalog. For every managed channel: if `autoSyncSupportedModels` is enabled, **stop for that channel** — report it and ask the user how to proceed (disable it first or skip the channel); never write a curated list while it is on.

### Apply the catalog plan, item by item

- **Channels** — for each planned channel: `updateChannel(id, input: { supportedModels: <exact plan list> })`. This is a wholesale replacement: legacy vendor-prefixed entries disappear with it. Prefix routing is that channel's own `auto-trim`/`modelMappings` setting, never the plan's or the agent's job. Do not merge with the remote list.
- **Models** — for each `models[]` entry in the plan: read the live model; if absent, `createModel` with the plan's `input` (then enable it); if present, `updateModel` with only the fields that differ. `CreateModelInput` requires `settings`, which the offline plan cannot carry: the executor supplies defaults — a `channel_model` rule pinning the model to its planned channel plus `disableDeveloperSettingsInheritance: false` and `default` load-balancer/trace-sticky strategies. When writing `remark`, parse the remote remark and keep its `manual` field — the plan's computed fields replace the old computed values only. For existing models, leave `settings` untouched except where the mapping section below applies.
- **Removals** — for each `removals[]` candidate: check every external (non-managed) channel's `supportedModels` and all models' associations (`channel_model.modelId`, `modelId.modelId`) for references. If any external use or reference exists: retain the object and report it under "retained". Otherwise `deleteModel(id)`.
- **Never write unmanaged objects**: a model that appears in the plan for one channel but has associations to other channels keeps those associations (guardrail 2) — when updating its `settings.associations`, replace only the rules that point at this managed channel.

### Apply the mapping table, item by item

- **Request models** — for each `models.csv` request row: read the live model and its target; both must exist and be enabled (a missing or disabled model is a per-item failure, reported, not fixed by creation). Then `updateModel` with `settings.associations` replaced by exactly one enabled `type=model` association targeting the confirmed candidate (`modelId: {modelId: <target>}`) — this is the one case where associations are wholesale-replaced, and it applies only to the fixed request models themselves.
- **Managed templates** — for each managed template from `config/model-decisions.json` `scope.templates` (`stable`/`claude`/`gpt`): rebuild `profile.modelMappings` as manual mappings (sources outside the fixed request set, preserved verbatim) ∪ the confirmed pairs (`claude-*` requests → the `claude` template, `gpt-*` → `gpt`, and `stable` mirrors the union). Send the full profile with `updateApiKeyProfileTemplate` — `UpdateAPIKeyProfileTemplateInput.profile` replaces the profile, so carry every non-mapping field (`channelIDs`, `channelTags`, quota, strategies) from the live read. If a template is missing entirely, ask before creating it.
- **Never touch manual mappings** whose sources are outside the request set, and never modify unrelated profile fields (guardrail 2).

### Verify by reading back (every write)

- Channels: re-read `supportedModels` — must equal the plan list exactly.
- Models: re-read the written fields — card values match the plan target; `remark` keeps the remote `manual` content with the plan's computed fields; associations match the intended shape (request models: exactly the one `type=model` rule; catalog models: this channel's rule added/replaced, everything else preserved).
- Templates: re-read the profile — `modelMappings` equals the desired mapping set; all non-mapping fields unchanged.
- Removals: re-read the deleted `modelID` — must be absent. A retained object must still exist.
- Routing (when the user asks or the write touches routing): `queryModelChannelConnections(associations: $assocs) { channel { id name } models { requestModel actualModel source } }` — done when the target channel resolves the expected `actualModel` with `source: mapping` or `direct`.

### Report

After all items: a per-item report — written+verified, unchanged (already correct), retained (external reference found), skipped (declined or blocked, with reason), failed (with the exact error). Failed and retained items are never silently dropped; the final state of every managed channel is echoed as the exact `supportedModels` list now live.

## Channel tags & non-fixed routing → `configure_channels.py` / `configure_models.py`

General channel maintenance (quota tags, ordering weights) and `channel_model` associations for models outside the fixed Claude/GPT request set. Channels are matched by exact name; server-assigned IDs are never the join key. Run with `--dry-run` first and show the user the diff; apply only after they confirm, then re-read channels/models and check the change landed only on the intended objects:

```bash
AXONHUB_JWT=<jwt> python3 axonhub-admin/scripts/configure_channels.py --dry-run
AXONHUB_JWT=<jwt> python3 axonhub-admin/scripts/configure_models.py --dry-run
```

`configure_models.py` skips the fixed Claude/GPT request models — their routing belongs to the mapping table above. Desired channel state lives in `CHANNELS` (`scripts/common.py`); tag/weight semantics and priority scoring are printed by the scripts themselves.

## Reference

### The model-ID gotcha (read before touching associations)

Upstream providers expose models under vendor-prefixed IDs (`deepseek/deepseek-v4-flash`, `zai-org/GLM-5.3`), while AxonHub Model entities use bare IDs (`deepseek-v4-flash`). Associations match **exact strings**, so a bare model ID never matches a prefixed channel entry on its own. Two fixes, often combined:

1. **Channel-side**: add `settings.modelMappings` (`from` = bare ID, `to` = prefixed ID). The `from` becomes a routable entry on that channel.
2. **Model-side**: use a `regex` association instead of exact `channel_model`/`model` bindings. Pattern `(?i)(^|/)deepseek-v4-flash$` matches the bare ID, any vendor prefix, and is case-insensitive. Escape dots (`kimi-k2\.7-code`).

Association types: `channel_model` (pinned channel + exact ID), `model` (exact ID across all channels), `regex` (global), plus channel-scoped/tagged variants. Prefer `regex` unless the user wants a model locked to specific channels.

### GraphQL input shapes

- `UpdateChannelInput.settings` replaces the entire settings object — pass `modelMappings` in full.
- `UpdateModelInput.settings.associations` replaces the entire list — fetch first, merge, write back.
- GraphQL IDs are GIDs (`gid://axonhub/Channel/12`); association inputs take plain ints for `channelId`.

### Known facts about this deployment

- Server: `https://axon.jasonqin.site` — the built-in default of every script; override with `AXONHUB_URL`.
- `AXONHUB_JWT` is the agreed env-var name for the live JWT. It is a credential: use it in Authorization headers, never write it into files, commits, or logs. Credentials live only in this skill — planning (`models-mapping`) is offline and needs none.
- The full admin schema to consult for exact field names: `internal/server/gql/*.graphql` in the axonhub repo.
