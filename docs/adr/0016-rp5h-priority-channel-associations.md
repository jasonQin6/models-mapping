---
status: accepted
---

# Non-Claude associations: rp5h-priority channel chains replace the global regex

The default association for a non-Claude global model was one global
`regex` rule at p0 matching the bare id on every channel, which scattered
traffic across all channels serving the id with no quality ordering. The
collected channels declare overlapping catalogs and per-model `rp5h`
(requests-per-5-hours headroom), and dedupe already trusts that number to
pick the owning channel.

Decisions:

- The planner emits `channelPriority` per plan model: every managed channel
  actively serving the canonical id, ordered by descending `rp5h` (null
  last, ties alphabetical), numbered from p0. p0 equals the dedupe winning
  channel.
- The association default for non-Claude models is a `channel_model` chain
  following that order — p0 primary, later entries the fallback order.
  Single-channel models degrade to a p0 pin.
- The intersection of `commandcode-goat` and `opencode-go` (post-alias) is
  the set of models with a real fallback chain; it is counted in
  `report.counts.intersection`. The static sections (`ant`, `sensenova`,
  ADR 0013) may appear as low-priority tail entries only through records
  they actually declare.
- The global-regex convention is retired; existing regex rules are
  replaced when a model's associations are next written. axonhub-admin
  executes the chain from `channelPriority` (guardrails unchanged:
  external associations are preserved, writes verified by read-back).

Consequences: routing follows measured headroom instead of uniform spray;
a channel whose `rp5h` drops loses the p0 seat on the next planned write.
