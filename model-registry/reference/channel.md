# Channel allowlists

The first AxonHub object this project plans: each managed channel's
`supportedModels`, the exact bare-ID allowlist. The AxonHub channel name
equals its `data/models_extra.json` section name, and the catalog plan's
`channels` section carries one exact, sorted native-id list per section.
Which channels are managed is recorded by the sections themselves
(ADR 0012); `ant` and `sensenova` are hand-maintained static sections no
watcher may write (ADR 0013).

The candidate universe starts from every record in the channel sections
(channel-provided `claude-*` models are never collected — Claude is
self-built in AxonHub, ADR 0012) and is reduced by:

- **Alias normalisation** — the hand-maintained top-level `aliases` map in
  `data/models_extra.json` (e.g. `tencent-hy3` -> `hy3`) merges
  cross-channel spellings. Channel lists keep the native id; the registry
  and `plan.models[]` use the canonical one, with `channelAliases`
  describing per-channel exposure for routing.
- **Speed-marketing variants** — ids ending `-fast`/`-highspeed` are
  derived as excluded at planning time.
- **Model decisions** — a hand-maintained `exclude` reason on a record
  keeps the model out; field-level merges preserve the reason across
  collection runs.
- **Cross-channel dedupe** — one winning channel per id: highest `rp5h`
  (null loses to a value, ties keep the alphabetically first channel).
  Every resolution reports `duplicate_model_across_sources`.
- **Variant grouping** — within a base model, `-free` beats
  `-contributor` beats the plain original; superseded variants leave with
  `variant_superseded`.
- **rp5h triage** — a non-free model missing `rp5h` is excluded when its
  Arena score is below 1500 (`rp5h_missing_excluded`); at or above 1500 it
  stays in the allowlist with a review warning
  (`rp5h_missing_review`) and is only ineligible for mapping-target
  selection.

Freeness is a declared channel fact — a hand-maintained `free: true` field
on the record, never the id spelling. Per owning channel, a free model's
`rp5h` is re-derived from the channel's largest non-free `rp5h` (falling
back to 1000 when the channel has no non-free basis) and a missing
`usage_quota` becomes 60 (`free_default_filled`).
