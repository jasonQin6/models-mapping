---
status: accepted
---

# Split persistent per-object plans replace the monolithic catalog plan

The planner used to emit one `/tmp` catalog plan (schema 3) mixing every
AxonHub object — channel allowlists, fully rendered model cards, remarks,
association chains, and all warnings — consumed in one wholesale
confirmation. The rendered `modelCard` duplicated `all_models.json`
field-by-field (snake_case transcribed to AxonHub camelCase), the artifact
lived outside the repository, and "confirm the plan" bundled unrelated
writes.

Decisions:

- Two generated artifacts, both committed under `data/` and owned by
  `model-registry` (regenerated whole on every run):
  - `data/channel-plan.json` (schema 1) — each channel's exact
    `supportedModels` desired state. It is the manual replacement for
    AxonHub's `autoSyncSupportedModels`: upstream auto-sync must stay off,
    and this list is the authoritative "models this channel may serve"
    source (see ADR 0010's push lineage and channel.md).
  - `data/model-plan.json` (schema 1) — incremental model entries:
    `cardRef` (the original `all_models.json` `vendor/model` key), merged
    cost end values, computed remark, derived meta
    (name/developer/icon/group), `channelAliases`, and the
    `channelPriority` chain (ADR 0016). No card content is copied.
- Write-time assembly lives in `model-registry/scripts/assemble_card.py`:
    it renders one plan entry into the full AxonHub input by reading the
    referenced card through `model_card()` — the single transcribing
    mapping, shared with planning. `cardRef: null` renders the default
    card.
- Warnings travel with the artifact they explain: allowlist warnings in
  the channel plan, card/remark warnings in the model plan; arena and
  mapping-workflow warnings stay run-level (stderr, `--fail-on-errors`).
- The `--plan-output` flag is retired; the planner writes both artifacts
  by default to `data/`.

Consequences: each AxonHub object is confirmed and written independently
(axonhub-admin's gate is now per-artifact); the plan diff no longer
drowns in transcribed card fields; `all_models.json` stays the only card
source. ADR 0014's "plan is not persisted" line is superseded for these
two artifacts — they are regenerable outputs like `models.csv`, not hand
edited.
