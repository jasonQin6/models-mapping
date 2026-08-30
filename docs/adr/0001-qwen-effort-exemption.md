---
status: superseded by ADR-0005
---

# Qwen models exempt from effort level extraction

> Historical decision. The source join and mapping workspace described here
> are retained as history; the three-source contract and current mapping
> boundary are defined by ADR-0005.

Qwen models use "max" in model names (e.g., qwen3.8-max) to denote model tier, not effort level. The effort extraction logic skips models starting with "qwen" to avoid conflating model names with capability tiers.

The exemption is configured via `EFFORT_EXEMPT_PREFIXES = ("qwen",)` in `scripts/name_matching.py`. If other providers adopt similar naming conventions, add their prefixes to this tuple.
