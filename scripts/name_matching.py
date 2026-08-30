#!/usr/bin/env python3
"""
Name matching module for cross-source model identification.

Provides normalization and matching functions to identify the same model
across different data sources (opencode mdx, arena leaderboard, CSV).

Public interface:
  - normalize(name) -> str: core normalization for model names
  - normalize_arena_name(name) -> (str, Optional[str]): arena-specific cleanup + effort extraction
  - find_best_match(csv_id, arena_lookup, is_free=False) -> (Optional[dict], str): 5-layer fallback chain
"""

import re
from typing import Dict, Optional, Tuple

# Qwen uses "max" in model names (e.g., qwen3.8-max), which conflicts with
# effort level extraction. Skip effort extraction for these prefixes.
EFFORT_EXEMPT_PREFIXES = ("qwen",)


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
    - Removes size suffixes (e.g., -70b, case-insensitive)
    - Extracts effort level (max/xhigh/ultra/high/medium/low) unless the
      model name starts with an exempt prefix (see EFFORT_EXEMPT_PREFIXES).
    
    Note: effort is expected as a suffix (e.g., "claude-opus-5-max"), not
    in parentheses. Parenthetical content is removed by normalize().
    
    Returns:
        (normalized_id, effort_level) where effort_level is None if not found
        or if the model is exempt.
    
    Examples:
        "Claude-Opus-5-max" -> ("claude-opus-5", "max")
        "Qwen3.8-max" -> ("qwen3.8-max", None)  # qwen exempt
        "GPT-5.4 Mini (20250320)" -> ("gpt-5.4-mini", None)
    """
    normalized = normalize(name)
    
    def strip_build_suffix(value: str) -> str:
        value = re.sub(r'-\d{8}$', '', value)
        return re.sub(r'-\d+[kb]$', '', value, flags=re.IGNORECASE)

    # Arena may place date/size before or after an effort suffix.
    normalized = strip_build_suffix(normalized)
    
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

    normalized = strip_build_suffix(normalized)
    
    return (normalized, effort)


def find_best_match(
    csv_id: str,
    arena_lookup: Dict[str, dict],
    is_free: bool = False
) -> Tuple[Optional[dict], str]:
    """Find the best matching arena entry for a CSV model_id.
    
    Implements a 5-layer fallback chain:
      1. Direct match
      2. Remove "-contributor" suffix
      3. Version downgrade (e.g., qwen3.7-plus -> qwen3.6-plus)
      4. Prefix match with wildcard (e.g., claude-haiku -> claude-haiku-*)
      5. Free model default (arena_score=0) if is_free=True
    
    Args:
        csv_id: normalized model_id from CSV
        arena_lookup: dict mapping arena model_id -> arena entry dict
        is_free: if True, return a default entry when no match found
    
    Returns:
        (arena_entry, match_type) where:
        - arena_entry: dict with keys {rank, rating, context, organization, effort}
          or None if no match found and not is_free
        - match_type: one of 'direct_match', 'contributor_suffix', 'version_downgrade',
          'prefix_match', 'free_default', 'no_match'
    """
    # Layer 1: Direct match
    if csv_id in arena_lookup:
        return (arena_lookup[csv_id], 'direct_match')
    
    # Layer 2: Remove -contributor suffix
    if csv_id.endswith('-contributor'):
        alt_id = csv_id[:-len('-contributor')]
        if alt_id in arena_lookup:
            return (arena_lookup[alt_id], 'contributor_suffix')
    
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
    
    # Layer 5: Free model default
    if is_free:
        return ({
            'rank': 0,
            'rating': 0,
            'context': '-',
            'organization': 'Unknown',
            'effort': None,
        }, 'free_default')
    
    return (None, 'no_match')
