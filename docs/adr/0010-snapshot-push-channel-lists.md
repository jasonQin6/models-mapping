---
status: accepted
---
# Snapshot-driven channel lists are pushed by a server timer, not pulled by AxonHub

The commandcode channel's `supportedModels` is the GOAT entitlement allowlist,
and `watch_goat.py` already produces it: the snapshot's `models` keys are the
fact source. Adding an intersection mechanism inside AxonHub
(`settings.modelListSourceURL`: fetch a JSON allowlist during each sync and
intersect it with the fetched `/v1/models` list) was implemented on an
axonhub feature branch but is rejected: it grows AxonHub's source for a
problem this repository already solves, and the intersection itself is a
fallback this data flow does not need — the entitlement page, not
`/v1/models`, decides the list.

Instead the channel's built-in upstream sync stays off (ADR 0009's existing
rule for managed channels) and a vol-server systemd timer pushes the
snapshot to AxonHub: pull the raw `data/goat-models.json` from this
repository's `main` branch (through the server's proxy), then run
`axonhub-admin/scripts/apply_channel_models.py`, which applies the sorted
`models` keys as `supportedModels` over the admin GraphQL API in one
`updateChannel` mutation that also forces `autoSyncSupportedModels: false`.
The script is idempotent (no-op when already in sync), read-before-write,
write-then-verify, and exits non-zero on any failure so the timer surfaces
it and the channel keeps its previous list until the next round.

Failure semantics move to the producer and the pusher, where they belong:

- Production-side hard gates in `watch_goat.py` (the snapshot is
  authoritative, so a partial parse must not publish): main-table rows
  skipped for missing columns, `to_model_id` collisions, zero models, or a
  count outside `expected_count` in `reference/goat/extra.json` all fail the
  run with no snapshot write — `main` keeps the last-good snapshot and the
  pusher keeps applying it as a no-op.
- GOAT-exclusive ids (no bare-id record in `data/all_models.json`) are kept
  with GOAT-claimed fields only instead of dropped: the GOAT page is the
  entitlement fact source, and there is no intersection to filter them
  anyway.

Ownership of the field is now unambiguous: `config/model-decisions.json`
gains `scope.external_channel_lists` (`["commandcode"]`), and
`sync_models.py` omits those channels' `supportedModels` from the catalog
plan while still planning their model cards — the interactive catalog flow
(ADR 0009) can never overwrite the pushed list again.

## Considered options

- **AxonHub pulls and intersects (`modelListSourceURL`)**: rejected —
  AxonHub source growth for zero data benefit; the intersection is a
  fallback the entitlement fact source makes unnecessary.
- **Publishing a separate allowlist artifact (`dist/goat-allowlist.json`)**:
  rejected — a second derived shape of the same fact; the snapshot itself is
  the allowlist.
- **Pushing from GitHub Actions**: rejected — credentials would live in
  GitHub Secrets and the write would leave the operator's infrastructure;
  the server timer keeps credentials on the server that runs AxonHub
  (ADR 0009's "CI never writes AxonHub" stands).
- **Keeping the catalog plan as the channel-list writer**: rejected — two
  writers for one field; the snapshot push is the authority, so the plan
  steps aside via `external_channel_lists`.

## Consequences

- AxonHub takes no source change for this flow; the deployed feature branch
  stays dormant (empty `modelListSourceURL`) or is simply not merged.
- Unattended AxonHub writes now exist in exactly one narrow shape: the
  snapshot push, scoped to `external_channel_lists` channels, touching only
  `supportedModels` and `autoSyncSupportedModels`. Everything else
  (catalog plans, mappings, templates) remains interactive per ADR 0009.
- `axonhub-admin` holds a second credential form on vol-server: a
  service-account email plus password file used to sign in for a fresh JWT
  each run (browser JWTs expire after 7 days and cannot head a timer).
  Deployment steps and systemd units: `axonhub-admin/deploy-vol-server-push.md`.
- `watch-pipeline`'s CI behavior is unchanged (crawl + commit only); its
  GOAT contract (`reference/goat/extra.json`) gains the `expected_count`
  gate and the keep-GOAT-exclusive rule.
- README, SECURITY.md, CONTEXT.md, and both SKILL.md files describe the
  push path; CONTEXT.md gains the Allowlist term.
