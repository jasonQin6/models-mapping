# Batch model creation

Read when rebuilding the catalog in bulk (after a wipe or migration); one-off
model writes follow the interactive program in SKILL.md instead. Tooling: the
`axonhub-cli` skill (`graphql-cli`), or raw GraphQL over `node:https`.

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
  (deployment quirk in SKILL.md).
