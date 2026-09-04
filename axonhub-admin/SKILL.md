---
name: axonhub-admin
description: Manage AxonHub (AI gateway) channels, models, model associations, and API key templates via its admin GraphQL API. Use when the user asks to view/change AxonHub channels or models, fix model-channel associations, add model mappings, or manage templates, without using the web UI.
---

# AxonHub Admin

Operate AxonHub over HTTP as an agent. All management operations live on one endpoint, `/admin/graphql`, and authenticate with a **JWT token** — never a static key. The token comes from the user's logged-in AxonHub browser tab.

## Step 1 — Obtain the token

The convention: the live JWT is read from the user's logged-in AxonHub browser tab and exported as `AXONHUB_ADMIN_TOKEN` for the rest of the run.

1. Check `AXONHUB_ADMIN_TOKEN` in the environment — if non-empty, use it (a token is valid for 7 days from sign-in).
2. Otherwise claim the user's AxonHub browser tab and read the token from page context: `localStorage.getItem('axonhub_access_token')`, then export it as `AXONHUB_ADMIN_TOKEN`.
3. If no logged-in tab exists, ask the user to sign in at `$AXONHUB_BASE_URL` (or supply credentials for `POST /admin/auth/signin` with `{email, password}` — the response contains the token). Then export it as `AXONHUB_ADMIN_TOKEN`.

Verify before proceeding:

```bash
curl -sS -X POST "$AXONHUB_BASE_URL/admin/graphql" \
  -H "Authorization: Bearer $AXONHUB_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "{ me { id email } }"}'
```

Done when: HTTP 200 with `data.me`. A 401 means the token expired or was cleared — fall back to step 3 of the list above instead of retrying.

## Step 2 — Read current state before mutating

Always fetch live data first — model IDs and channel IDs drift:

- Channels: `channels(first: 50) { edges { node { id name type status supportedModels manualModels baseURL settings { modelMappings { from to } } } } } }`
- Models: `models(first: 100) { edges { node { id modelID developer status settings { associations { type channelModel { channelId modelId } modelId { modelId } regex { pattern } } } } } } }`
- API-key profile templates: `apiKeyProfileTemplates { id name ... }` (see the schema in `internal/server/gql/axonhub.graphql` of the axonhub repo for exact fields).

Done when: you can name the target channel's ID, its exact `supportedModels` entries, and each affected model's `modelID` and current associations.

## Step 3 — Mutate, then verify the association

Apply changes with `updateChannel` (e.g. `settings.modelMappings`) or `updateModel` (e.g. `settings.associations`), then verify the routing actually resolves, because a written mapping and a working route are different facts:

```graphql
query Check($assocs: [ModelAssociationInput!]!) {
  queryModelChannelConnections(associations: $assocs) {
    channel { id name }
    models { requestModel actualModel source }
  }
}
```

Done when: the target channel appears in the result with the expected `actualModel`, and `source` is `mapping` (via mapping) or `direct`.

## Applying a reviewed catalog plan

`models-mapping/scripts/sync_models.py` produces a self-contained plan JSON (creates/updates/
deletes/channelUpdates with fingerprints). Execute it only after the user has
reviewed that plan file and explicitly confirmed it:

```bash
python3 axonhub-admin/scripts/apply_catalog_plan.py \
  --plan-input /tmp/catalog-plan.json \
  --source data/goat-models.json \
  --model-decisions config/model-decisions.json \
  --axonhub-url "$AXONHUB_BASE_URL" \
  --token "$AXONHUB_ADMIN_TOKEN"
```

Without `--apply` the script validates the plan shape, re-checks source
fingerprints, and prints a summary; it performs no mutations. With
`--apply --verify` it re-reads all before-state for drift, executes the
mutations (new models are created then enabled), and reads back every written
value. A stale plan (source changed, remote drifted) is refused.

Done when: `verify` prints `"ok": true`. Exit code 3 means verification found
mismatches — report them, do not retry blindly.

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

- Server: `https://axon.jasonqin.site` (`$AXONHUB_BASE_URL`, set in `~/.zshrc`).
- `AXONHUB_ADMIN_TOKEN` (also in `~/.zshrc`, normally empty) is the agreed env-var name for the live JWT. It is a credential: use it in Authorization headers, never write it into files, commits, or logs.
- The full admin schema to consult for exact field names: `internal/server/gql/*.graphql` in the axonhub repo.
