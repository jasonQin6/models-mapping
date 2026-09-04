# OpenCode AxonHub sync contract

## Sources

`data/models.json` is a schema-versioned, normalized snapshot of provider cache
nodes. It preserves the raw provider model set; inclusion is decided later by
the cache/go intersection.

`data/go.json` is schema version 1 and is keyed by exact model ID. It supplies
`protocol`, `rp5h`, `usage_quota`, `context_threshold`, `peak_hours`, and
`retention`. Source IDs join exactly—Arena-style fallbacks are not used.

`config/model-decisions.json` owns:

- the three managed channel names and three managed template names;
- reviewed `exclude` or `supplement` decisions for intersection models;
- optional canonical mapping overrides.

Every manual decision requires a reason. Generated snapshots and CSV files are
not decision sources.

## OpenCode Go partition

```text
included = cache(opencode-go) ∩ go.json - manual excludes

completions → opencode-go
responses   → op-responses
messages    → op-anthropic
```

A model-level `api.npm`, when present, must agree with the npm implied by the
go protocol. A mismatch or missing protocol is blocking. Missing RP5H creates a
decision-required item; optional metadata may remain null.

## AxonHub reads and writes

Relay queries page through all channels and models. Model reads include complete
settings/associations so unmanaged rules can be preserved and exact references
to delete targets can be detected.

Model mutations manage `name`, `developer`, `icon`, `group`, `modelCard`,
`remark`, and the managed portion of `settings.associations`. For an included
candidate, remove only `channel_model` rules pointing at managed channel IDs,
preserve every other rule, then append one canonical rule for the
protocol-derived managed channel.

Channel mutations contain only `supportedModels` and are limited to names in
the configured managed scope. `manualModels`, credentials, endpoints, tags,
quotas, default test model, and ordering remain unchanged. Removing a default
test model is blocking.

Excluded models are removed from managed `supportedModels`. Unmanaged channel
support makes them `externallyRetained`. Exact `model`/`channel_model`
references block deletion. Only an existing model with neither condition may
enter `deleteModel`, and the complete channel/association state is
fingerprinted before review.

## Managed remark

AxonHub's string `remark` contains canonical JSON:

```json
{
  "manual": "operator note",
  "rp5h": 1000,
  "usage_quota": 60,
  "context_threshold": 200000,
  "peak_hours": "09:00-18:00",
  "retention": 30
}
```

Preserve `manual` from valid JSON; preserve legacy plain text by moving it into
`manual`. Regenerate controlled fields from go data plus reviewed supplements.

## Plan invariants

Plans record source hashes, model/channel before fingerprints, included and
excluded evidence, external retention, blocking references, model/channel
diffs, and a proposed normalized snapshot. Apply re-reads all before-state and
refuses stale or edited plans. The snapshot is written only after successful
apply and verification.
