---
status: accepted
---
# One registry store, one planner skill; CI collects, agents compute, humans confirm

Through ADR 0005–0011 the pipeline accreted four channel snapshots, two
planner scripts, and four derived artifacts (`models.csv`,
`data/enriched.json`, catalog plans, `data/model_select.json`).  Channel
snapshots carried full model cards that were duplicated from models.dev,
`watch_go.py` had to write quota fields twice (top level plus an `extra`
mirror) to satisfy two consumers with different reading conventions, the same
model listed by both channels entered the catalog twice, and channel-provided
`claude-*` models competed with the self-built Claude models that actually
serve downstream traffic.

Decisions:

1. **`data/models_extra.json` is the single store of channel-declared
   facts.**  Each collector owns one `channels.<channel>` section (go and
   goat serialize in CI, goat after go).  Records carry only what the channel
   itself declares: `rp5h`, `usage_quota`, `cost{}`, the remark thresholds
   the source states (`context_threshold`, `peak_hours`, `retention`), and
   `tok_s` for goat.  No card data is collected at all.
2. **models.dev (`data/all_models.json`) is the only card source**, consumed
   at planning time.  Channel `cost` values win field-by-field where present.
3. **Cross-channel dedupe**: a model id listed by several channels belongs to
   the channel with the highest `rp5h` (null loses to a value, ties keep the
   alphabetically first channel); every resolution is a warning.
4. **Claude is never taken from a channel.**  Channel-provided `claude-*`
   models are not collected.  Downstream Claude requests are served by
   self-built AxonHub global models mapped (Arena formula, baseline routing)
   to third-party candidates; the mapping target can be retargeted in the
   AxonHub UI without touching any agent.  GPT request models are
   pass-through and never enter the mapping.
5. **All planning converges in the `model-registry` skill**
   (`models_mapping.py`): dedupe, free fill, cards, Claude mapping,
   `models.csv`, and the schema-2 catalog plan (unchanged shape, so the
   axonhub-admin execution procedure stands).  Computing stays out of CI:
   `build-mapping.yml` is retired, the Watch Pipeline keeps only collection
   and moves to a every-3-days cadence.
6. **Retired**: the `models-mapping` skill (`build_mapping.py`,
   `sync_models.py`, `select.py`), `data/enriched.json` (free-fill
   provenance now travels as planner warnings), `apply_channel_models.py`
   and the vol-server push line — ADR 0010's unattended write is withdrawn
   and the commandcode channel list returns to the interactive write path.

## Considered options

- **Keep the four-snapshot/two-script pipeline**: rejected — card duplication
  and the dual-write hack existed only to serve planner internals that are
  now one module.
- **Union with first-source-wins dedupe**: rejected — a model could enter the
  catalog through a channel whose quota values are strictly worse for the
  same id.
- **Let CI run the planner (rebuild `models.csv` on every crawl)**:
  rejected — computing is an agent-driven review step, not a data fact;
  keeping it out of CI keeps the write path fully interactive.
- **An intersection/allowlist feature inside AxonHub**: rejected again, for
  the reasons in ADR 0010.

## Consequences

- `watch_go.py`/`watch_goat.py` shrink to channel-declared facts only;
  `--models-dev`/`--all-models` inputs disappear and with them the double
  write of ADR 0011.
- The candidate universe loses channel `claude-*` models by design; the
  seven ADR-0011 models stay out (their go.mdx rows never existed).
- Free-model quotas are re-derived per owning channel at planning time, so
  the rule has exactly one owner; `data/enriched.json` disappears.
- The vol-server operator must dismantle the push units manually
  (`axonhub-admin/deploy-vol-server-push.md` becomes the retirement guide).
- ADR 0010's push mechanism and ADR 0011's `--fill-source` planner input are
  superseded; ADR 0011's core decision (go.mdx owns the opencode-go list)
  survives as the go collector's contract.
