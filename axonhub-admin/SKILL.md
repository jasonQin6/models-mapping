---
name: axonhub-admin
description: Execute this repository's governed AxonHub write program — confirmed catalog plans (channel supportedModels, model cards), mapping tables (request-model associations), batch model creation, and the deployment's routing patterns and quirks. Use for any AxonHub work within the managed scope; generic one-off resource queries from the command line belong to the axonhub-cli skill.
---

# AxonHub Admin

Operate AxonHub over HTTP as an agent. All management operations live on one endpoint, `/admin/graphql`, and authenticate with a **JWT token** — never a static key. The token comes from the user's logged-in AxonHub browser tab.

## Step 1 — Obtain the token

The live JWT is read from a logged-in AxonHub browser session via the
**browser-use skill**, then used for the rest of the run:

1. Check `AXONHUB_JWT` in the environment — if non-empty, use it (valid for 7 days from sign-in).
2. Otherwise open `https://axon.jasonqin.site/` with the browser-use skill and read the token from page context: `localStorage.getItem('axonhub_access_token')`. GraphQL calls can then be issued directly from page context (`fetch("/admin/graphql", …)`, same-origin) or exported as `AXONHUB_JWT` for curl. On the Node side of the browser-use kernel `fetch` is not a global — issue the call from page context, or use `node:https` with the exported token.
3. If no logged-in session exists, ask the user to sign in first (or supply credentials for `POST /admin/auth/signin` with `{email, password}` — the response contains the token).

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

1. **Touch managed channels only.** The managed set is the channel sections of `data/models_extra.json` (`ant`, `commandcode-goat`, `opencode-go`, `sensenova`); the AxonHub channel name equals the section name. Nothing outside it is created, updated, or deleted — even when the GraphQL response makes it easy. API-key profile templates are not managed by this project: the former `stable`/`claude`/`gpt` maintenance flow is retired (ADR 0014), and template mappings live directly in the AxonHub UI.
2. **Preserve unmanaged associations and external references.** Association lists are replaced per model: keep every rule that does not belong to this write. A model object referenced by an external (non-managed) channel's `supportedModels`, or by any association of another model, is never deleted — retain it and report.
3. **Read before write.** Fetch the live object immediately before each mutation. Never write from the plan alone or from a cached read; live state is the only write basis.
4. **Write then verify; retry only with verification.** Read back every write and compare field by field. This deployment intermittently rejects valid payloads with `unknown field` (see quirks below); such failures may be retried with a short backoff — writes are wholesale replacements, so re-issuing the same confirmed input is idempotent, and every retry is followed by a fresh read-back. Persistent errors are shape problems (e.g. a bare `when` condition), not flakes: fix the input shape or report the item, never retry blindly.

### Confirmation (AskUserQuestion)

Two independent confirmations; confirming one never authorizes the other:

- **Catalog plan** — the offline plan JSON from `model-registry/scripts/models_mapping.py` (schema 3, desired state): per-channel exact bare-ID `supportedModels`, target model-card values for every included model, `warnings`.
- **Mapping table** — `models.csv` (the only mapping review artifact): `request → mapping` rows for the fixed request models.

For each confirmation, show the artifact next to live state (current channel lists, current request-model targets) so the diff is visible, then ask. Execute only the confirmed set; anything the user declines is skipped and reported.

### Read remote state (before any write)

Fetch channels, models, and API-key profile templates in full. Note: this
deployment's server errors on a channels query combining `tags` with
`settings` — fetch them in two queries and join by `id`:

- Channels: `channels(first: 50) { edges { node { id name type status supportedModels manualModels autoSyncSupportedModels baseURL orderingWeight tags } } } }` and `channels(first: 50) { edges { node { id settings { modelMappings { from to } } } } }`
- Models: `models(first: 100) { edges { node { id modelID name developer status remark modelCard { reasoning { supported default } toolCall temperature modalities { input output } vision cost { input output cacheRead cacheWrite } limit { context output } knowledge releaseDate lastUpdated } settings { disableDeveloperSettingsInheritance loadBalancerStrategy traceStickyMode associations { type priority disabled channelModel { channelId modelId } modelId { modelId } regex { pattern } } } } } } }`
- API-key profile templates: `apiKeyProfileTemplates(first: 50) { edges { node { id name linkedProfilesCount profile { name modelMappings { from to } channelIDs channelTags channelTagsMatchMode modelIDs loadBalanceStrategy traceStickyMode quota { … } } } } }` — Relay connections require an explicit `first:` and `edges { node { … } }` wrapping; `apiKeyProfileTemplates` without `first` fails with `either first or last must be provided`.

Take the **exact upstream model IDs** from the channels query's `supportedModels` — never from UI chips, screenshots, or memory. A wrong ID (e.g. `sensenova-v1-fast` for the upstream's `sensenova-u1-fast`) creates a model entity that silently routes nowhere.

Done when: you can name each managed channel's ID, its exact `supportedModels`, and each affected model's `modelID`, status, and current associations.

### Execution-time check: autoSyncSupportedModels must be off

AxonHub's hourly upstream sync overwrites a channel's `supportedModels` with `manualModels ∪ 上游全量`, which erases the curated catalog. For every managed channel: if `autoSyncSupportedModels` is enabled, **stop for that channel** — report it and ask the user how to proceed (disable it first or skip the channel); never write a curated list while it is on.

### Apply the catalog plan, item by item

- **Channels** — for each planned channel: `updateChannel(id, input: { supportedModels: <exact plan list> })`. This is a wholesale replacement: legacy vendor-prefixed entries disappear with it. Prefix routing is that channel's own `auto-trim`/`modelMappings` setting, never the plan's or the agent's job. Do not merge with the remote list. **Exception — static free channels (`ant`, `sensenova`, ADR 0013):** the plan's `supportedModels` for them contains only the channel-exclusive models (dedupe keeps one winning channel per canonical ID), so wholesale-applying it would strip the shared models they also serve. Their authoritative list is the hand-maintained `models_extra.json` static section plus live state; only association/card writes for their exclusive models follow the normal flow.
- **Models** — for each `models[]` entry in the plan: read the live model; if absent, `createModel` with the plan's `input` (then enable it); if present, `updateModel` with only the fields that differ. `CreateModelInput` requires `settings`, which the offline plan cannot carry: the executor supplies defaults — a `channel_model` rule pinning the model to its planned channel plus `disableDeveloperSettingsInheritance: false` and `default` load-balancer/trace-sticky strategies. When writing `remark`, parse the remote remark and keep its `manual` field — the plan's computed fields replace the old computed values only. For existing models, leave `settings` untouched except where the mapping section below applies.
- **Retiring a live global model** — the plan has no removals section (ADR 0014): when a model leaves the plan and the user confirms it should go, check every external (non-managed) channel's `supportedModels` and all models' associations (`channel_model.modelId`, `modelId.modelId`) for references; retain and report on any external use, otherwise `deleteModel(id)` as a standalone confirmed operation.
- **Never write unmanaged objects**: a model that appears in the plan for one channel but has associations to other channels keeps those associations (guardrail 2) — when updating its `settings.associations`, replace only the rules that point at this managed channel.

### Apply the mapping table, item by item

- **Request models** — for each `models.csv` request row: read the live model and its target; both must exist and be enabled (a missing or disabled model is a per-item failure, reported, not fixed by creation). Then `updateModel` with `settings.associations` replaced by exactly one enabled `type=model` association targeting the confirmed candidate (`modelId: {modelId: <target>}`) — this is the one case where associations are wholesale-replaced, and it applies only to the fixed request models themselves.
- **Templates are out of scope** — the former managed-template rebuild (`stable`/`claude`/`gpt` `modelMappings`, ADR 0014-retired) is no longer part of the write program; never modify profile templates while executing a confirmed plan.
- **Never touch manual mappings** whose sources are outside the request set, and never modify unrelated profile fields (guardrail 2).

### Verify by reading back (every write)

- Channels: re-read `supportedModels` — must equal the plan list exactly.
- Models: re-read the written fields — card values match the plan target; `remark` keeps the remote `manual` content with the plan's computed fields; associations match the intended shape (request models: exactly the one `type=model` rule; catalog models: this channel's rule added/replaced, everything else preserved).
- Retired models: re-read the deleted `modelID` — must be absent. A retained object must still exist.
- Routing (when the user asks or the write touches routing): `queryModelChannelConnections(associations: $assocs) { channel { id name } models { requestModel actualModel source } }` — done when the target channel resolves the expected `actualModel` with `source: mapping` or `direct`.

### Report

After all items: a per-item report — written+verified, unchanged (already correct), retained (external reference found), skipped (declined or blocked, with reason), failed (with the exact error). Failed and retained items are never silently dropped; the final state of every managed channel is echoed as the exact `supportedModels` list now live.

## Channel lists are interactive-only

Every managed channel's `supportedModels` is written through the interactive
catalog-plan flow above (ADR 0012). The former unattended vol-server push
(`apply_channel_models.py`, ADR 0010) is retired; server-side teardown steps
live in `deploy-vol-server-push.md`.

## Batch model creation via GraphQL (proven 2026-09-09)

Bulk (re)creation after a catalog wipe: payloads come from the
`models_extra.json ∩ all_models.json` intersection, cards from
`all_models.json`. Tooling: the `axonhub-cli` skill (`graphql-cli`), or raw
GraphQL over `node:https`.

- Mutations MUST pass the input as **variables** (`-v '{"input": …}'`); an
  inline JSON object literal is invalid GraphQL (`Expected Name, found
  String`).
- **CLI output is noisy** (npm notices) and its JSON is not always cleanly
  parseable — a mutation can succeed on the server while local parsing
  reports failure. After every pass, reconcile against live state
  (`models(first: 100)`) and only re-issue what is actually missing.
- **Soft-deleted rows block creation.** Deleted models are invisible to
  `models` but keep their ID: `createModel` fails with `model name 'x'
  already exists`. They are still enumerable via `node(id:
  "gid://axonhub/Model/<n>") { … on Model { modelID } }` — the node lookup
  bypasses the soft-delete interceptor, so probing ascending IDs rebuilds
  the full inventory. Purge with `deleteModel(id)`: the resolver context
  skips the soft-delete interceptor, making it a hard delete.
  `bulkDeleteModels(ids)` returned `true` but was observed NOT to purge —
  verify every purge by the succeeding `createModel`, never by the return
  value.
- **Enable after create**: `createModel` and the web UI both start models
  `disabled`; flip with `updateModelStatus(id, enabled)`.
- Association wiring is a separate pass after creation
  (`settings: {associations: []}` in the payload); request-model routing
  then follows the mapping table.

Payload conventions (per model, card data from `all_models.json`):

- `developer` — the models.dev vendor prefix normalized to AxonHub's English
  vendor vocabulary: `zai-org`/`zhipuai` → `zai`, `meituan` → `longcat`,
  `moonshotai` → `moonshot`, `deepseek-ai` → `deepseek`.
- `icon` — lobe-icons name per vendor (DeepSeek, ChatGLM, Qwen, Moonshot,
  XAI, Hunyuan, LongCat, XiaomiMiMo, Meta, NVIDIA, Step, Gemini, OpenAI);
  empty string when unsure.
- `group` — the card's `family`.
- `type` — `chat`; image-generation endpoints (e.g. `sensenova-u1-fast`,
  `POST /v1/images/generations`, no image input, not Chat Completions) are
  `image_generation` with modalities `input: [text]` / `output: [image]`,
  `vision: false`, and are excluded from the chat registry via the
  `models_extra.json` record `exclude` flag.
- `modelCard` — `reasoning: {supported, default}` ← `reasoning`; `toolCall`
  ← `tool_call`; `temperature` ← `temperature` (default true); `vision` ←
  `image` in modalities.input; `modalities` and `limit` verbatim; `cost` ←
  `{input, output, cacheRead: cache_read, cacheWrite: cache_write}`;
  `knowledge`, `releaseDate` ← `release_date`, `lastUpdated` ←
  `last_updated` when present.
- `settings` — `{associations: []}`; omit the optional strategy fields
  (deployment quirk above).

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
- Association rules that pass live validation (one JSON object per rule):
  - pinned channel: `{type: "channel_model", priority: 0, disabled: false, channelModel: {channelId: <int>, modelId: "<upstream id>"}}`
  - global regex with channel exclusion: `{type: "regex", priority: 0, disabled: false, regex: {pattern: "(?i)(^|/)glm-5\\.2$", exclude: [{channelIds: [<int>]}]}}`
  - exact-ID with exclusion: `{type: "model", priority: 0, modelId: {modelId: "<id>", exclude: [{channelIds: [<int>]}]}}`
  - time-gated (see quirks — root condition must be a group): `when: {enabled: true, condition: {type: "group", logic: "AND", conditions: [{type: "condition", field: "daily_time", operator: "within", value: "22:00-08:00"}]}}`
- `updateModel` can rename (`modelID`), retitle (`name`), and flip `status` (lowercase enums `enabled`/`disabled`/`archived`). Models created via `createModel` or the web UI start **disabled** and must be enabled separately.

### Routing patterns

The association conventions — fallback chains, cross-channel pools,
priority demotion, time-gated routing, free-variant merging (e.g.
`ling-3.0-flash` → ant's `vl`/`sante`/`fin`) — are owned by
`model-registry/reference/associations.md`; this skill executes the
confirmed shapes and verifies every one with
`queryModelChannelConnections(associations: $assocs) { channel { id name } models { requestModel actualModel source } }`.

### Deployment quirks (verified 2026-09-09)

- A `when` whose **root condition is a bare `condition`** is rejected with `invalid when condition: root when condition must be a group` — always wrap the leaf condition in `{type: "group", logic: "AND", conditions: [...]}`. Cross-midnight ranges (`22:00-08:00`) are supported; times are server-local.
- Transient `unknown field` errors (`GRAPHQL_VALIDATION_FAILED`, erratic `variable.input.*` paths, sometimes pointing at untouched sibling fields) appear during apparent deploy windows and disappear on their own; identical payloads succeed afterwards. Handle per guardrail 4: retry with backoff + read-back, don't reshape on a flake.
- Omit optional `settings` fields (`disableDeveloperSettingsInheritance`, `loadBalancerStrategy`, `traceStickyMode`) when unchanged — omitted fields keep their live values, and passing them unnecessarily was observed to trip the transient validator.
- When input shapes misbehave, explore the **live** schema first: prefer the `axonhub-cli` skill's `find <type> -e axonhub --input --detail`; raw introspection (`__type(name: "ModelSettingsInput") { inputFields { name } }`) is the fallback when the CLI is unavailable. The deployment can drift from the local `internal/server/gql/*.graphql` snapshot in either direction.

### Known facts about this deployment

- Server: `https://axon.jasonqin.site` — the built-in default of every script; override with `AXONHUB_URL`.
- `AXONHUB_JWT` is the agreed env-var name for the live JWT. It is a credential: use it in Authorization headers, never write it into files, commits, or logs. Credentials live only in this skill — planning (`model-registry`) is offline and needs none.
- Ad-hoc queries, schema discovery (`find`), and CLI-based operations: use the **axonhub-cli** skill (`graphql-cli`) — it owns the generic tool mechanics; this skill owns only this repository's write program and conventions. The full admin schema lives at `internal/server/gql/*.graphql` in the axonhub repo.
