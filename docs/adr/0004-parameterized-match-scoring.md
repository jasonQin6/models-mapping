---
status: superseded by ADR-0005
---

# Parameterized match scoring with pure function extraction

> Historical decision. Its pure-function testing lesson is retained, but the
> current scoring inputs and ownership are defined by ADR-0005.

The match scoring weights are now defined in `DEFAULT_WEIGHTS` dict and passed to `score_match()` as a parameter. The scoring logic is extracted as a pure function for testability.

**Why parameterize:** Previously, 6 magic numbers (SCORE_W, RP5H_W, etc.) were hardcoded at module scope. The function's interface didn't reveal it was a weighted scoring formula. Testing "what if SCORE_W = 0.4" required editing source. Now weights are a single dict, easy to tune or load from config.

**Why pure function:** `score_match()` takes request_score, candidate, max_values, and weights as parameters, returns a float. No side effects, no global state. Trivially testable: assert specific scores for known inputs.

**Why max_score_diff protection:** When all candidates have the same arena score, max_score_diff = 0, causing division by zero. Now handled: proximity defaults to 1.0 when max_score_diff = 0.
