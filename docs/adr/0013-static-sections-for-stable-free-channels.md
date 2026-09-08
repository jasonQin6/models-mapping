---
status: accepted
---

# Static hand-maintained sections for stable free channels

Free upstreams whose model lists barely change (Ant Digital `ant`, SenseNova
`sensenova`) enter `data/models_extra.json` as hand-maintained static
sections instead of gaining a watch channel. The section-ownership rule is
otherwise unchanged: each watcher still exclusively rewrites its own channel
section, and a static section is owned by this repository's human/agent
curation, never by a watcher.

Rationale: a collector is justified when the upstream signals new models
(entitlement pages, `/v1/models`); for these two providers the list is
stable and was configured by hand in AxonHub, so a watcher would only
re-verify a constant. The registry still needs the rows — `plan_from`
rejects `models_extra` channels outside `scope.channels`, so a static
section requires registering the provider→channel pair in
`config/model-decisions.json` like any managed channel.

Static-section records carry `name` plus null remark fields (no rp5h /
usage_quota source exists); such models surface in the plan report as
`rp5h_missing_review` / `ineligible` and never win cross-channel dedupe
against a channel that has an rp5h value. The catalog plan's per-channel
`supportedModels` therefore lists only the exclusive models of a static
channel — applying channel lists wholesale to `ant` / `sensenova` from a
plan is forbidden; the authoritative list for those channels is the static
section itself plus live AxonHub state.

## Considered options

- **A watch channel per provider**: rejected — collects a constant, adds a
  job that can only fail.
- **Leaving the channels out of the registry**: rejected — `plan_from` hard
  fails on unknown sections, and mapping/plan artifacts would silently
  ignore half of the gateway's free capacity.
- **Fabricating rp5h to make the models score**: rejected — dishonest data
  in the scoring pipeline; review-flagging is the honest state.

## Consequences

- `config/model-decisions.json` scope gains `ant` and `sensenova`;
  `models_extra.json` gains the two static sections.
- AGENTS.md notes the static-section exception next to the
  section-ownership constraint.
- Revisit if either provider starts rotating models often enough that a
  watcher beats manual curation.
