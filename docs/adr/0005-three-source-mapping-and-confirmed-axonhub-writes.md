---
status: accepted
---

# Separate source collection from confirmed three-channel AxonHub writes

OpenCode cache, go.mdx, and Arena change independently. Keep them as separate
schema-versioned snapshots and let one deterministic builder generate the small
mapping workspace. For `opencode-go`, the managed catalog is the exact
cache/go intersection minus reviewed model decisions; cache-only and go-only
models are evidence, not usable catalog members.

This repository owns only `opencode-go`, `op-responses`, `op-anthropic`, their
included candidate models, fixed Claude/GPT request associations, and the
`stable`, `claude`, `gpt` templates. It may read other channels to prevent unsafe
deletion, but never mutates them. Protocol partitions come from each go.mdx
model: completions, responses, and messages map to the three managed channels.
A conflicting model-level npm is blocking rather than silently preferred.

`models-mapping` (sync_models.py) plans reconciliation of managed `supportedModels`, model
cards, structured remarks, and exactly one managed `channel_model` association
per included candidate while preserving every unmanaged association. Excluded
models are removed only from managed channels. Objects used by external
channels are retained; exact association references block deletion; only
unreferenced, externally unused objects can enter a confirmed delete plan.
Execution of that plan belongs to `axonhub-admin`
(see ADR 0007).

`models-mapping` applies one canonical target per fixed request model to its
global `type=model` association and to the managed templates. `claude` and `gpt`
preserve mappings outside the fixed request set; `stable` is their complete
union plus any reported stable-only manual mappings. Existing non-mapping
template fields are preserved, and all other templates are outside scope.

`config/model-decisions.json` is the human fact source for managed scope,
intersection-model exclude/supplement decisions, and canonical mapping
overrides. Generated JSON/CSV artifacts never own manual decisions. Missing
protocol or request Arena evidence is blocking; missing mapping-critical RP5H
is decision-required; optional metadata may remain null.

Catalog and mapping are separate mutation plans. Each records source and remote
fingerprints, shows a compact impact summary, requires explicit confirmation,
and verifies its own writes. Confirmation of one plan never authorizes the
other.

## Considered options

- **Cache/go union**: rejected because retired cache entries were reintroduced.
- **Provider npm for every model**: rejected because provider metadata cannot
  distinguish the model's actual OpenCode endpoint protocol.
- **One merged CSV as source of truth**: rejected because collectors overwrite
  one another and generated output cannot safely own human decisions.
- **Automatic deletion on source absence**: rejected because global model
  objects may still be used by channels outside this repository's boundary.
- **One template-specific target per policy**: deferred; the current phase uses
  one canonical target and preserves mappings outside the maintained request set.
