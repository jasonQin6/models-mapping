---
status: accepted
---

# Arena match strictness: variant inheritance only, borrowed scores rejected

The planner's arena matching for candidates accepted the full
`find_best_match` chain and filled every gap: a `version_downgrade` put the
previous version's score on a newer id, a `prefix_match` put a name family's
best score on one member, and a `no_match` model defaulted to 1500. Review
lists built on those scores overstated the pool — experimental models
without any board presence passed an `>= 1500` filter on the default alone.

Decisions:

- Accepted matches are `direct_match` and the same-model variant-suffix
  layer (`-contributor`, `-free`, `-vl` inherit the base record).
- `version_downgrade` and `prefix_match` are rejected: the score belongs to
  a different model. Warning: `arena_borrowed_rejected`.
- A non-free model with no match carries no score at all
  (`arena_missing`); the `DEFAULT_ARENA_SCORE = 1500` fill is retired. An
  unscored model is listed in `models.csv` with an empty `arena_score` and
  never enters the mapping formula's candidate pool; with no `rp5h` either,
  the missing-rp5h triage excludes it outright.
- Free models keep the 1500 default (`free_defaulted`): free fill must stay
  able to pair every request regardless of board coverage.

Claude request models are unaffected: a request without arena evidence
remains the blocking `request_arena_missing` error it always was.

Consequences: mapping suggestions can only shift when a previously
defaulted/borrowed candidate was actually winning (none did — such
candidates scored at or below the real pool); the plan's warnings list
names every rejected borrow for manual arena assignments via
`data/arena.json`.
