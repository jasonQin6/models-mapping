---
status: accepted
---

# Config retirement: data moves to models.csv and models_extra.json

The two hand-maintained config inputs, `config/request-models.json` and
`config/model-decisions.json`, are retired. Each carried a different kind
of truth, and each truth moves next to the data it governs; the `config/`
directory disappears. Field-level ownership becomes the ownership model for
`data/models_extra.json` records.

## What moved where

- **Request-model list** → the `role=request` rows of `models.csv`. The
  mapping workspace was already the review artifact for those rows; now its
  request rows are hand-maintained (add a row to add a request, delete to
  retire) and the only cells the planner reads back. Every other cell,
  including the whole `mapping` column, is regenerated each run — the CSV
  is for confirmation, not for pinning. There is no override mechanism: the
  formula + free-fill result is the suggestion, and day-to-day retargeting
  stays in the AxonHub UI.
- **Manual excludes** → a hand-maintained `exclude` reason on the record in
  `data/models_extra.json` (the former `model-decisions.json` actions). The
  speed-variant stamping (`-fast`/`-highspeed`) that collectors used to
  write into that field moves to the planning layer as a derived rule, so
  `exclude` has exactly one writer: the human. The plan no longer carries a
  `removals` section — retiring a live AxonHub model is a standalone
  confirmed axonhub-admin operation.
- **Managed channel scope** → the `models_extra.json` channel sections
  themselves; the AxonHub channel name equals the section name. The
  `scope.channels` provider→channel map (whose `commandcode-goat →
  commandcode` entry had drifted from the live channel's actual name) and
  its validation are gone; the plan's `channels` are keyed by section name
  and the `providers` field is dropped (schema 3).
- **Managed templates** → nowhere. The `stable`/`claude`/`gpt`
  `scope.templates` maintenance flow is retired outright; the live template
  objects stay untouched and template mappings are managed directly in the
  AxonHub UI.
- **Request-id aliasing** → dropped. `claude-haiku-4.5` was renamed to
  `claude-haiku-4-5` (matching the Arena board id) instead of maintaining an
  `arena_model_id` alias map; a request missing from the board gets a hand
  assignment via a `manual: true` record in `data/arena.json`, replacing the
  unused request `arena_score` pin.

## Field-level ownership for models_extra.json

Collectors previously rewrote their whole channel section, which would have
erased any hand-maintained record field. `update_channel` now merges per
record: an id the channel still declares keeps its hand-maintained fields
(anything the scrape does not produce, e.g. `exclude`) while its contract
fields refresh; an id that leaves the channel declaration leaves the
section with its whole record. The contracts in
`watch-pipeline/reference/<channel>/extra.json` enumerate the collector-
owned fields.

## Considered options

- **Keeping `scope.channels` as config**: rejected — the mapping had
  already drifted from the live deployment (the channel is named
  `commandcode-goat`), and identity naming removes a whole class of
  join-by-name failures.
- **Pinning confirmed mappings back into `models.csv`** (sticky `mapping`
  cells): rejected — the CSV exists to be confirmed, not curated; stickiness
  would silently freeze suggestions after the first run.
- **Splitting `exclude` into collector vs manual fields**: rejected —
  deriving the speed-variant rule at planning time leaves one field with
  one owner and stores no derived state.

## Consequences

- `models.csv` has a dual identity (hand-maintained request rows,
  generated everything else); AGENTS.md records the split, and the planner
  fails loudly when the file is missing.
- plan schema 3: `removals` and `providers` are gone; axonhub-admin's
  write program drops the removals and managed-template steps.
- Revisit if request models ever need per-request metadata that does not
  fit the five-column workspace — the fix is a column, not a new config
  file.
