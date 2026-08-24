#!/usr/bin/env python3
"""
Compute model mapping for Claude/GPT models.
Updates the mapping column in models.csv.

Reads:
  - models.csv (root): contains opencode models + request models with arena data

Updates:
  - mapping column for request models (provider != "opencode")

Logic:
  - Group request models by series (claude-*, gpt-*)
  - For each series, the model with lowest arena_score is the "cheap" model
  - Cheap models route to free opencode models or highest quota
  - Other models use proximity-based scoring
"""

import csv
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants — Proximity formula weights
# ---------------------------------------------------------------------------

SCORE_W = 0.30
RP5H_W = 0.25
USAGE_W = 0.15
PROXIMITY_W = 0.30
PENALTY_K = 0.2
UPGRADE_BONUS = 0.1

# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> List[dict]:
    """Read models.csv and return list of rows."""
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def safe_float(value: str, default: float = 0.0) -> float:
    """Safely convert string to float."""
    if not value or value == '':
        return default
    try:
        return float(value)
    except ValueError:
        return default


def safe_int(value: str, default: int = 0) -> int:
    """Safely convert string to int."""
    if not value or value == '':
        return default
    try:
        return int(value)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Series detection
# ---------------------------------------------------------------------------

def extract_series(model_id: str) -> str:
    """Extract series name from model_id.
    
    Examples:
        claude-opus-5 -> claude
        gpt-5.6-sol -> gpt
        gpt-5.4-mini -> gpt
    """
    # Match patterns like claude-*, gpt-*
    match = re.match(r'^(claude|gpt)', model_id.lower())
    if match:
        return match.group(1)
    return ''


def group_by_series(request_models: List[dict]) -> Dict[str, List[dict]]:
    """Group request models by series."""
    groups = {}
    for model in request_models:
        series = extract_series(model['model_id'])
        if series:
            if series not in groups:
                groups[series] = []
            groups[series].append(model)
    return groups


def find_cheap_model(models: List[dict]) -> Optional[str]:
    """Find the model with lowest arena_score in a series."""
    if not models:
        return None
    
    # Sort by arena_score ascending
    sorted_models = sorted(models, key=lambda m: safe_float(m.get('arena_score', '')))
    return sorted_models[0]['model_id']


# ---------------------------------------------------------------------------
# Mapping algorithm
# ---------------------------------------------------------------------------

def compute_mapping_for_request_model(
    request_model_id: str,
    request_score: float,
    opencode_models: List[dict],
) -> Optional[str]:
    """Compute the best opencode model mapping for a request model."""
    
    # Filter to opencode models with valid arena_score
    candidates = []
    for m in opencode_models:
        if m.get('provider') == 'opencode':
            score = safe_float(m.get('arena_score', ''), 0)
            if score > 0:
                candidates.append(m)
    
    if not candidates:
        return None
    
    # Get max values for normalization
    max_score = max(safe_float(m.get('arena_score', '')) for m in candidates) or 1
    max_rp5h = max(safe_int(m.get('rp5h', '')) for m in candidates) or 1
    max_usage = max(safe_float(m.get('usage_quota', '')) for m in candidates) or 1
    
    # Compute score difference range
    scores = [safe_float(m.get('arena_score', '')) for m in candidates]
    max_score_diff = max(scores) - min(scores) if len(scores) > 1 else 1
    
    scored = []
    for model in candidates:
        cand_score = safe_float(model.get('arena_score', ''))
        proximity = 1.0 - abs(cand_score - request_score) / max_score_diff
        
        # Penalty: only when candidate is worse (downgrade)
        penalty = 0.0
        if cand_score < request_score:
            penalty = PENALTY_K * (request_score - cand_score) / max_score_diff
        
        # Upgrade bonus: when candidate is better
        upgrade = 0.0
        if cand_score > request_score:
            upgrade = UPGRADE_BONUS
        
        rp5h = safe_int(model.get('rp5h', ''))
        usage_quota = safe_float(model.get('usage_quota', ''))
        
        rp5h_ratio = math.log(rp5h + 1) / math.log(max_rp5h + 1)
        usage_ratio = usage_quota / max_usage
        
        match_score = (SCORE_W * (cand_score / max_score)
                       + RP5H_W * rp5h_ratio
                       + USAGE_W * usage_ratio
                       + PROXIMITY_W * proximity
                       - penalty
                       + upgrade)
        
        scored.append((model['model_id'], match_score))
    
    scored.sort(key=lambda x: -x[1])
    
    if scored:
        return scored[0][0]
    
    return None


def main():
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    
    models_csv_path = repo_root / "models.csv"
    
    if not models_csv_path.exists():
        print(f"Missing: {models_csv_path}", file=sys.stderr)
        sys.exit(1)
    
    # Read models.csv
    rows = read_csv(models_csv_path)
    
    if not rows:
        print("Error: models.csv is empty", file=sys.stderr)
        sys.exit(1)
    
    # Separate opencode models and request models
    opencode_models = [r for r in rows if r.get('provider') == 'opencode']
    request_models = [r for r in rows if r.get('provider') != 'opencode']
    
    if not opencode_models:
        print("Error: No opencode models found", file=sys.stderr)
        sys.exit(1)
    
    if not request_models:
        print("Warning: No request models found", file=sys.stderr)
        sys.exit(0)
    
    # Group request models by series
    series_groups = group_by_series(request_models)
    
    # Find cheap model for each series
    cheap_models = {}
    for series, models in series_groups.items():
        cheap = find_cheap_model(models)
        if cheap:
            cheap_models[series] = cheap
            print(f"Series '{series}': cheap model = {cheap}", file=sys.stderr)
    
    # Find free opencode models
    free_models = [m for m in opencode_models if 'free' in m['model_id'].lower()]
    highest_quota_model = max(opencode_models, key=lambda m: safe_int(m.get('rp5h', '')))
    
    # Compute mapping for each request model
    mapping_count = 0
    for row in request_models:
        model_id = row['model_id']
        series = extract_series(model_id)
        arena_score_str = row.get('arena_score', '')
        
        if not arena_score_str or arena_score_str == '':
            print(f"Warning: {model_id} has no arena_score, skipping", file=sys.stderr)
            continue
        
        try:
            arena_score = float(arena_score_str)
        except ValueError:
            print(f"Warning: {model_id} has invalid arena_score: {arena_score_str}", file=sys.stderr)
            continue
        
        # Check if this is the cheap model for its series
        is_cheap = (series in cheap_models and cheap_models[series] == model_id)
        
        if is_cheap:
            # Route cheap models to free models or highest quota
            if free_models:
                best_free = max(free_models, key=lambda m: safe_int(m.get('rp5h', '')))
                row['mapping'] = best_free['model_id']
            else:
                row['mapping'] = highest_quota_model['model_id']
        else:
            # Compute mapping using proximity formula
            target = compute_mapping_for_request_model(model_id, arena_score, opencode_models)
            if target:
                row['mapping'] = target
            else:
                print(f"Warning: Could not compute mapping for {model_id}", file=sys.stderr)
        
        mapping_count += 1
    
    # Write back to models.csv
    fieldnames = list(rows[0].keys())
    
    with open(models_csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"Updated mapping for {mapping_count} request models", file=sys.stderr)
    print(f"Written: {models_csv_path}", file=sys.stderr)
    
    # Print summary
    for row in request_models:
        if row.get('mapping'):
            series = extract_series(row['model_id'])
            is_cheap = (series in cheap_models and cheap_models[series] == row['model_id'])
            marker = " (cheap)" if is_cheap else ""
            print(f"  {row['model_id']:25s} -> {row['mapping']}{marker}", file=sys.stderr)


if __name__ == "__main__":
    main()
