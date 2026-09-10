# Model cards

The second AxonHub object this project plans: each registry model's card
and remark. The generated `data/model-plan.json` (schema 1) is
**incremental** (ADR 0017) — one entry per model with `modelID`, owning
channel, optional `channelAliases`, the `channelPriority` association
chain (ADR 0016), `cardRef`, and an `input` of derived meta plus
channel-declared end values. The card itself is never copied into the
plan.

- **One card source** — `data/all_models.json` (the models.dev snapshot)
  is the only card source and never a list source: it fills facts for a
  channel-claimed model but never adds one. `cardRef` is the original
  `vendor/model` key; `cardRef: null` marks a card-less model
  (`card_missing`).
- **Write-time assembly** — `.agents/skills/model-registry/scripts/assemble_card.py`
  renders one plan entry into the full AxonHub input: descriptive
  `modelCard` fields from the referenced card via `model_card()` (the
  single transcribing mapping, shared with planning), the plan's `cost`
  and `remark` as end values. A card-less model renders the default card
  (reasoning/toolCall false, temperature true, text modalities, zero
  limits).
- **Channel cost wins** — the plan's `cost` starts from the card, then
  each channel-declared field (`input`, `output`, `cache_read`,
  `cache_write`) overwrites field by field; null channel values keep the
  card value.
- **Free zeroes silent prices** — a `free: true` record is the channel
  declaring every price zero: cost fields it leaves silent start at 0
  instead of the card's list price; declared values still win field by
  field.
- **Nothing is invented** — a model with no card is planned from
  channel-claimed data alone and reported as `card_missing`.
- **Remark** — the plan recomputes the structured fields (`rp5h`,
  `usage_quota`) from the owning channel's record — missing ones reported
  as `missing_remark_fields` — and carries a `manual` field; the writer
  keeps the remote `manual` content and replaces only the computed values
  (axonhub-admin's write program).

Every planned model is `type: chat`; image-generation endpoints are kept
out of the registry by the record's hand-maintained `exclude` flag
(see [channel.md](channel.md)), not by a card rule.
