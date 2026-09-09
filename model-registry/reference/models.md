# Model cards

The second AxonHub object this project plans: each registry model's card
and remark, delivered as `plan.models[]` — canonical id, owning channel,
optional `channelAliases`, and the `input` the write path applies.

- **One card source** — `data/all_models.json` (the models.dev snapshot)
  is the only card source and never a list source: it fills facts for a
  channel-claimed model but never adds one.
- **Channel cost wins** — the card's `cost` starts, then each
  channel-declared field (`input`, `output`, `cache_read`, `cache_write`)
  overwrites field by field; null channel values keep the card value.
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
