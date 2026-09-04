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

It originally stayed outside the `build-mapping` candidate intersection.
ADR 0007 superseded that rule: the candidate universe is now the union of
`opencode-go-models.json` and `goat-models.json` minus reviewed decisions, and
GOAT snapshot changes feed `build-mapping` rebuilds like the other inputs.

The snapshot changes independently of `opencode-go`, so its job depends only
on `fetch-all-models` and runs unconditionally once its input is fresh.
