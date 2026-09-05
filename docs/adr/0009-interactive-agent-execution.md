---
status: accepted
---

# Interactive confirmation with agent-executed writes, retiring the apply scripts

The plan pipeline — plan JSON with source fingerprints, drift refusal, and
dedicated apply scripts — is heavier than a single-operator system needs.
Computation stays deterministic and offline: `models-mapping` scripts turn
the snapshots plus `config/model-decisions.json` (rules in
`data/formula.md`) into the plan JSON and `models.csv`. The CSV remains the
only mapping review artifact; no parallel markdown review document is
generated, so each fact has one representation.

Confirmation is interactive: the user reviews the plan content and the CSV in
the session and confirms through a structured question. The `axonhub-admin`
agent then reads live state, applies the confirmed changes item by item over
GraphQL, verifies every write by reading it back, and reports per-item
results. `apply_catalog_plan.py` and `apply_mapping.py` retire; their
guardrails become the documented agent procedure: touch managed channels
only, preserve unmanaged associations and externally referenced objects,
read before write, verify after write, no speculative retries. AxonHub writes
happen only in interactive agent sessions — CI never writes AxonHub.

The plan JSON survives as the machine-readable change set between
computation and execution: per channel, an exact bare-ID `supportedModels`
target list applied wholesale. There are no modes, no append-only rule, and
no fingerprints. The vendor-prefix preservation problem that motivated
append-only semantics is dissolved by deployment convention: model
identifiers are bare everywhere, the channels' own auto-trim prefix settings
handle prefix routing, and any legacy prefixed entries are simply deleted
and rebuilt by the replace. This supersedes ADR 0007's append-only rule for
the `commandcode` channel. `sync_models.py` thereby becomes fully offline
and no longer needs a read-only JWT.

The fate of `configure_channels.py` / `configure_models.py` for routine
channel ops (agent-direct GraphQL versus agent-invoked scripts) is deferred
pending field experience; they remain until that comparison is made. Channel
tags are display metadata; channels carry no routing priority.

## Considered options

- **Keeping the apply scripts**: rejected — fingerprint and drift machinery
  is disproportionate; read-before-write at execution time covers drift.
- **Agent computes everything live**: rejected — the rules lose testability
  and reproducibility, and `data/formula.md` plus the test suite would guard
  nothing.
- **Markdown review document**: rejected — it duplicates facts already in
  the CSV and plan JSON.

## Consequences

- README, SECURITY.md, AGENTS.md, and the skill docs replace plan-file
  confirmation and apply-script entries with the interactive flow.
- Credentials become exclusive to `axonhub-admin`; the read-only JWT for
  planning disappears.
- Tests covering the retired scripts and plan fingerprints retire with
  them.
- Managed channels must keep `autoSyncSupportedModels` disabled: AxonHub's
  hourly upstream sync overwrites `supportedModels` with the full fetched
  list plus manual models, which would erase the curated catalog. The
  agent checks this at execution time and reports instead of writing when
  it is enabled.
