# Repository Guidelines

用中文回复用户。

## Architecture

This repository separates source collection from confirmed AxonHub writes:

- `scripts/watch_go.py` owns `data/go.json`.
- `scripts/watch_arena.py` owns `data/arena.json`.
- `opencode-axonhub-sync` owns the normalized provider snapshot in
  `data/models.json` and AxonHub catalog reconciliation.
- `scripts/build_mapping.py` is the only writer of `models.csv`.
- `models-mapping` reviews the generated mapping and, after explicit user
  confirmation, applies one `type=model` association per fixed request model.
- `axonhub-config` retains generic channel administration.

Read `CONTEXT.md` for canonical terminology and
`docs/adr/0005-three-source-mapping-and-confirmed-axonhub-writes.md` before
changing an ownership boundary.

## Generated Contracts

- `data/models.json`, `data/go.json`, and `data/arena.json` use
  `schema_version: 1` envelopes.
- `config/request-models.json` is the maintained fixed request-model set.
- `config/model-decisions.json` owns managed scope and reviewed human decisions.
- `models.csv` is generated and has exactly:
  `model_id,role,arena_score,rp5h,mapping`.

Do not hand-edit `models.csv` or make a source watcher write another source's
artifact.

## Commands

```bash
python3 scripts/watch_go.py --output data/go.json
python3 scripts/watch_arena.py --output data/arena.json
python3 scripts/build_mapping.py --model-decisions config/model-decisions.json --fail-on-errors
python3 -m pytest -q
python3 -m pytest -q axonhub-config/tests opencode-axonhub-sync/tests
```

Validate changed skills with:

```bash
python3 /Users/jason/.codex/skills/.system/skill-creator/scripts/quick_validate.py models-mapping
python3 /Users/jason/.codex/skills/.system/skill-creator/scripts/quick_validate.py opencode-axonhub-sync
```

## Coding and Safety

- Python 3.12+, standard library only, type hints on public signatures.
- Preserve source IDs exactly between `models.json` and `go.json`; Arena alone
  may use the documented fallback chain.
- Cache/go one-sided models are excluded. Missing mapping-critical RP5H is
  decision-required; invalid schemas/protocols/scope or request evidence are blocking.
- Run AxonHub mutations only from a reviewed plan after explicit confirmation.
- Never print or commit JWTs, API keys, SQLite secrets, or token-bearing plans.
- Preserve unrelated dirty-worktree changes.

## Project Metadata

- Issues: `docs/agents/issue-tracker.md`
- Triage labels: `docs/agents/triage-labels.md`
- Domain documentation: `docs/agents/domain.md`
