#!/usr/bin/env python3
"""
Name matching module for cross-source model identification.

Provides normalization and matching functions to identify the same model
across data sources (arena leaderboard, registry ids, the mapping CSV).
All normalization happens here, in the planning layer; collectors store
raw names.
"""

import re
from typing import Dict, Optional, Tuple

# Qwen uses "max" in model names (e.g., qwen3.8-max), which conflicts with
# effort level extraction. Skip effort extraction for these prefixes.
EFFORT_EXEMPT_PREFIXES = ("qwen",)

# Variant suffixes the matching chain may strip to inherit the base model's
# Arena score. Series names (-flash, -max) and speed marketing (-fast,
# -highspeed, excluded upstream) must never enter: stripping them would put
# one real model's score onto a different model. A new marketing suffix
# (e.g. -vl once it spreads) is promoted here after human triage of the
# `unrecognized_variant_suffix` warning.
MATCH_VARIANT_SUFFIXES = ("-contributor", "-free", "-vl")


def normalize(name: str) -> str:
    """Normalize a model name to a standardized id.
    
    Removes parenthetical suffixes, converts to lowercase, replaces spaces
    with hyphens, and collapses consecutive hyphens.
    
    Examples:
        "Claude Opus 5" -> "claude-opus-5"
        "GPT-5.4 Mini (20250320)" -> "gpt-5.4-mini"
        "Qwen3.7-Plus (256K context)" -> "qwen3.7-plus"
    """
    name = re.sub(r'\s*\([^)]+\)', '', name)
    name = name.strip().lower().replace(' ', '-')
    name = re.sub(r'-+', '-', name)
    return name


def normalize_arena_name(name: str) -> Tuple[str, Optional[str]]:
    """Normalize an arena leaderboard model name and extract effort level.

    Calls normalize() first, then applies arena-specific cleanup:
    - Removes date suffixes (e.g., -20250320)
    - Extracts effort level (max/xhigh/ultra/high/medium/low) unless the
      model name starts with an exempt prefix (see EFFORT_EXEMPT_PREFIXES).

    Parameter-size suffixes (e.g., -27b) are part of the board's model
    identity and are kept verbatim; the leaderboard lists full ids such as
    ``qwen3.8-27b`` and stripping them collides distinct models.

    Note: effort is expected as a suffix (e.g., "claude-opus-5-max"), not
    in parentheses. Parenthetical content is removed by normalize().

    Returns:
        (normalized_id, effort_level) where effort_level is None if not found
        or if the model is exempt.

    Examples:
        "Claude-Opus-5-max" -> ("claude-opus-5", "max")
        "Qwen3.8-max" -> ("qwen3.8-max", None)  # qwen exempt
        "GPT-5.4 Mini (20250320)" -> ("gpt-5.4-mini", None)
        "qwen3.8-27b" -> ("qwen3.8-27b", None)
    """
    normalized = normalize(name)

    def strip_date_suffix(value: str) -> str:
        return re.sub(r'-\d{8}$', '', value)

    # Arena may place a date before or after an effort suffix.
    normalized = strip_date_suffix(normalized)
    
    # Extract effort level (unless exempt)
    effort = None
    is_exempt = any(normalized.startswith(prefix) for prefix in EFFORT_EXEMPT_PREFIXES)
    
    if not is_exempt:
        for eff in ("max", "xhigh", "ultra", "high", "medium", "low"):
            suffix = f"-{eff}"
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
                effort = eff
                break

    normalized = strip_date_suffix(normalized)

    return (normalized, effort)


def find_best_match(
    csv_id: str,
    arena_lookup: Dict[str, dict],
) -> Tuple[Optional[dict], str]:
    """Find the best matching arena entry for a CSV model_id.

    Implements a 4-layer fallback chain:
      1. Direct match
      2. Known variant suffix (contributor/free/vl) -> base model
      3. Version downgrade (e.g., qwen3.7-plus -> qwen3.6-plus)
      4. Prefix match with wildcard (e.g., claude-haiku -> claude-haiku-*)

    A free model with no match carries no default here: the planner applies
    its own default score (1500, ``arena_defaulted``) so every free model
    stays reachable for free fill.

    Args:
        csv_id: normalized model_id from CSV
        arena_lookup: dict mapping arena model_id -> arena entry dict

    Returns:
        (arena_entry, match_type) where:
        - arena_entry: dict with keys {rating, organization, effort}
          or None if no match found
        - match_type: one of 'direct_match', 'variant_suffix', 'version_downgrade',
          'prefix_match', 'no_match'
    """
    # Layer 1: Direct match
    if csv_id in arena_lookup:
        return (arena_lookup[csv_id], 'direct_match')

    # Layer 2: Known variant suffix -> base model
    for suffix in MATCH_VARIANT_SUFFIXES:
        if csv_id.endswith(suffix):
            base_id = csv_id[: -len(suffix)]
            if base_id in arena_lookup:
                return (arena_lookup[base_id], 'variant_suffix')
    
    # Layer 3: Version downgrade
    match = re.match(r'^(.+?)(\d+)\.(\d+)(.*)$', csv_id)
    if match:
        prefix = match.group(1)
        major = int(match.group(2))
        minor = int(match.group(3))
        suffix = match.group(4)
        
        if minor > 0:
            alt_id = f"{prefix}{major}.{minor - 1}{suffix}"
            if alt_id in arena_lookup:
                return (arena_lookup[alt_id], 'version_downgrade')
    
    # Layer 4: Prefix match with wildcard
    candidates = []
    for arena_id, entry in arena_lookup.items():
        if arena_id.startswith(f"{csv_id}-"):
            candidates.append(entry)
    
    if candidates:
        return (max(candidates, key=lambda e: e['rating']), 'prefix_match')

    return (None, 'no_match')


def unrecognized_variant_suffix(model_id: str) -> Optional[str]:
    """Return the trailing token when it looks like a new variant suffix.

    A candidate that matches nothing while ending in an alphabetic
    ``-token`` outside ``MATCH_VARIANT_SUFFIXES`` probably carries a
    marketing suffix the registry has never seen; the planner surfaces it
    as an ``unrecognized_variant_suffix`` warning for human triage instead
    of silently defaulting the score. Versioned tails (``-a55b``,
    ``-0902``, ``qwen3.8-27b``) are parameter/size naming, never flagged.
    """
    if "-" not in model_id:
        return None
    tail = model_id.rsplit("-", 1)[-1]
    if not tail or not tail.isascii() or not tail.isalpha():
        return None
    if f"-{tail}" in MATCH_VARIANT_SUFFIXES:
        return None
    return tail
