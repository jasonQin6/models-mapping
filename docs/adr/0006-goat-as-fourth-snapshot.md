---
status: accepted
---

# GOAT as a fourth public snapshot outside the mapping intersection

`data/goat-models.json` (`commandcode-goat` provider, api.json-shaped) is a
fourth public snapshot alongside `all_models.json`, `opencode-go-models.json`,
and `arena.json`. It is collected by the `watch-goat-models` job in
`watch-pipeline.yml` via `watch-pipeline/scripts/watch_goat.py`, enriched from
the single source `data/all_models.json`, and maintained via the
`watch-pipeline` skill (channel contract in
`watch-pipeline/reference/goat/extra.json`).

It does not enter the `build-mapping` candidate intersection, which remains
the exact `all_models ∩ opencode-go` minus reviewed model decisions. GOAT
changes therefore do not trigger `build-mapping` rebuilds; they serve as
decision reference and a future consumption source.

The snapshot changes independently of `opencode-go`, so its job depends only
on `fetch-all-models` and runs unconditionally once its input is fresh.
