---
name: models-mapping
description: Review and apply the canonical one-to-one Claude/GPT mapping to AxonHub request-model associations and the managed stable, claude, and gpt profile templates. Use after the deterministic mapping workspace is rebuilt.
---

# Models Mapping

Maintain one canonical target for each fixed Claude/GPT request model. Apply it
to request `type=model` associations and the managed `stable`, `claude`, `gpt`
templates. Catalog/model-card/channel synchronization belongs to
`opencode-axonhub-sync`.

## Rebuild and validate

From the repository root, reproduce the generated workspace and report:

```bash
review_dir="$(mktemp -d)"
python3 models-mapping/scripts/build_mapping.py \
  --model-decisions config/model-decisions.json \
  --output "$review_dir/models.csv" \
  --report-output "$review_dir/report.json" \
  --fail-on-errors
cmp models.csv "$review_dir/models.csv"
```

The CSV must have exactly
`model_id,role,arena_score,rp5h,mapping`. Every enabled fixed request model must
appear once with a non-empty target that names an eligible candidate. Missing
request Arena evidence, unresolved decisions, unknown targets, or source/schema
drift are blocking.

## Preview

Generate a read-only AxonHub plan:

```bash
python3 axonhub-config/scripts/apply_mapping.py \
  --mapping-file models.csv \
  --request-models config/request-models.json \
  --model-decisions config/model-decisions.json \
  --axonhub-url "$AXONHUB_URL" \
  --plan-output /tmp/models-mapping-plan.json \
  --dry-run
```

Show the user:

- each changed request model's current and proposed target;
- association/template changed/no-op counts;
- per-template added/changed/removed counts;
- total linked profiles affected;
- warnings and blocking errors;
- the plan hash.

Keep the terminal summary compact. Full before/after mappings, linked-profile
counts, state fingerprints, and stable-only manual mappings stay in the plan
JSON. Any blocking error prevents apply.

## Template merge contract

- `claude` replaces mappings for maintained Claude request IDs and preserves
  mappings whose request IDs are outside the fixed set.
- `gpt` does the same for maintained GPT IDs. Create it with
  default/default/any when absent.
- `stable` contains the complete post-merge `claude ∪ gpt` mapping.
- Preserve stable-only manual mappings and report them as warnings; never
  silently delete them.
- Existing template fields other than `modelMappings` are preserved.
- `free`, `ali-coding`, and every other template are outside this skill.
- Manual mappings may target models outside the three managed OpenCode
  channels; preserve them without catalog validation.

## Association contract

Each maintained request model must already exist and be enabled. Its association
set becomes exactly one enabled priority-0 `type=model` rule pointing at the
canonical target. This global rule scans all enabled channels exposing the
target, so client and channel API protocols may differ. Preserve non-association
settings; clear legacy request `channel_model` rules. Candidate associations are
owned by catalog sync.

## Apply and verify

Only after the user explicitly confirms the displayed plan hash:

```bash
python3 axonhub-config/scripts/apply_mapping.py \
  --plan-input /tmp/models-mapping-plan.json \
  --request-models config/request-models.json \
  --model-decisions config/model-decisions.json \
  --axonhub-url "$AXONHUB_URL" \
  --apply
```

Apply re-reads all request models and managed templates. Any changed settings,
template profile, target status, identity, or plan hash makes the plan stale.
After mutation, verify every request association and all three template
profiles. Report partial failures without guessing retries or modifying
unrelated objects.

Mapping confirmation never authorizes the separate catalog sync plan.
