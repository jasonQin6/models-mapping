#!/usr/bin/env python3
"""
Compute model mapping from models.csv + arena leaderboard.
Outputs CSV for user review and server-side consumption.

Reads:
  - models.csv (root): opencode model data
  - references/leaderboard.json: arena leaderboard data
"""

import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants — Models
# ---------------------------------------------------------------------------

CLAUDE_MODELS = [
    "claude-opus-5",
    "claude-fable-5",
    "claude-sonnet-5",
]

GPT_MODELS = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
]

CHEAP_MODELS = ["claude-haiku", "gpt-5.4-mini"]
CHEAP_MODELS_SET = set(CHEAP_MODELS)

CHANNEL_ID = 7

# ---------------------------------------------------------------------------
# Constants — Proximity formula weights
# ---------------------------------------------------------------------------

SCORE_W = 0.30
RP5H_W = 0.25
USAGE_W = 0.15
PROXIMITY_W = 0.30
PENALTY_K = 0.2
UPGRADE_BONUS = 0.1

EFFORT_LEVELS = {"max", "xhigh", "ultra", "high", "medium", "low"}


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

def strip_parens(name: str) -> str:
    return re.sub(r'\s*\([^)]+\)\s*$', '', name).strip()


def strip_effort(name: str) -> Tuple[str, Optional[str]]:
    if name.lower().startswith("qwen"):
        return name, None
    for effort in EFFORT_LEVELS:
        suffix = f"-{effort}"
        if name.endswith(suffix):
            return name[:-len(suffix)], effort
    return name, None


def normalize_opencode_id(model_id: str) -> str:
    return re.sub(r'-contributor$', '', model_id)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> Dict[str, dict]:
    """Read models.csv and return dict keyed by model_id."""
    if not path.exists():
        return {}
    models = {}
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            model_id = row.get('model_id', '').strip()
            if not model_id:
                continue
            # Convert numeric fields
            models[model_id] = {
                'name': row.get('name', ''),
                'protocol': row.get('protocol', ''),
                'rp5h': int(row['rp5h']) if row.get('rp5h') else 0,
                'rpw': int(row['rpw']) if row.get('rpw') else 0,
                'rpm': int(row['rpm']) if row.get('rpm') else 0,
                'usage_quota': float(row['usage_quota']) if row.get('usage_quota') else 0,
                'price_output': float(row['price_output']) if row.get('price_output') else 0,
                'max_price_output': float(row['max_price_output']) if row.get('max_price_output') else 0,
                'price_input': float(row['price_input']) if row.get('price_input') else 0,
                'price_cached_read': float(row['price_cached_read']) if row.get('price_cached_read') else 0,
                'price_cached_write': float(row['price_cached_write']) if row.get('price_cached_write') else 0,
                'context_threshold': row.get('context_threshold', '-'),
                'peak_hours': row.get('peak_hours', '-'),
                'retention': int(row['retention']) if row.get('retention') else 0,
            }
    return models


def read_json(path: Path):
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Mapping algorithm
# ---------------------------------------------------------------------------

def generate_mapping(
    opencode_models: Dict[str, dict],
    arena_data: List[dict],
) -> Tuple[Dict[str, dict], List[dict]]:
    """Generate mapping with proximity formula + asymmetric penalty + upgrade bonus."""
    
    # Build Arena lookup
    arena_lookup = {}
    for entry in arena_data:
        norm_id = entry["model_id"]
        effort = entry.get("effort")
        if norm_id not in arena_lookup or entry["rating"] > arena_lookup[norm_id][1]:
            arena_lookup[norm_id] = (entry["rank"], entry["rating"], effort, entry.get("context", "-"))

    # Match OpenCode models to Arena entries
    opencode_with_arena = {}
    for oc_id, oc_info in opencode_models.items():
        norm_id = normalize_opencode_id(oc_id)
        if norm_id in arena_lookup:
            rank, score, effort, context = arena_lookup[norm_id]
            opencode_with_arena[oc_id] = {
                "quota": oc_info["rp5h"],
                "usage_quota": oc_info.get("usage_quota", 0),
                "arena_rank": rank,
                "arena_score": score,
                "arena_effort": effort,
                "context": context,
            }

    # Find best Arena entry for each fixed series
    def find_best_arena_for_series(series_name):
        best = None
        for norm_name, (rank, score, effort, context) in arena_lookup.items():
            if norm_name == series_name or norm_name.startswith(f"{series_name}-"):
                if best is None or score > best[1]:
                    best = (rank, score, effort, context, norm_name)
        return best

    # Build fixed_models_data for ALL models
    fixed_models_data = []
    for series in CLAUDE_MODELS + GPT_MODELS + CHEAP_MODELS:
        result = find_best_arena_for_series(series)
        if result:
            rank, score, effort, context, matched_name = result
            fixed_models_data.append({
                "series": series,
                "arena_rank": rank,
                "arena_score": score,
                "arena_context": context,
                "effort": effort or "-",
            })
        else:
            fixed_models_data.append({
                "series": series,
                "arena_rank": None,
                "arena_score": None,
                "arena_context": "-",
                "effort": "-",
            })

    # Greedy weighted mapping for scored models
    max_quota_all = max(m["rp5h"] for m in opencode_models.values()) if opencode_models else 1
    max_usage_all = max(m.get("usage_quota", 0) for m in opencode_models.values()) or 1
    highest_quota_model = max(opencode_models.keys(), key=lambda m: opencode_models[m]["rp5h"])

    candidates = {mid: info for mid, info in opencode_with_arena.items()}
    cand_scores = [info["arena_score"] for info in candidates.values()]
    max_score = max(cand_scores) if cand_scores else 1
    min_score = min(cand_scores) if cand_scores else 0
    max_score_diff = max_score - min_score if max_score != min_score else 1

    sortable_models = [
        fm for fm in fixed_models_data
        if fm["arena_score"] is not None and fm["series"] not in CHEAP_MODELS_SET
    ]
    sortable_models.sort(key=lambda x: -x["arena_score"])

    assignments = {}

    for fm in sortable_models:
        series = fm["series"]
        src_score = fm["arena_score"]
        effort = fm["effort"]

        scored = []
        for mid, info in candidates.items():
            cand_score = info["arena_score"]
            proximity = 1.0 - abs(cand_score - src_score) / max_score_diff

            penalty = 0.0
            if cand_score < src_score:
                penalty = PENALTY_K * (src_score - cand_score) / max_score_diff

            upgrade = 0.0
            if cand_score > src_score:
                upgrade = UPGRADE_BONUS

            rp5h_ratio = math.log(info["quota"] + 1) / math.log(max_quota_all + 1)
            usage_ratio = info.get("usage_quota", 0) / max_usage_all

            match = (SCORE_W * (cand_score / max_score)
                     + RP5H_W * rp5h_ratio
                     + USAGE_W * usage_ratio
                     + PROXIMITY_W * proximity
                     - penalty
                     + upgrade)

            scored.append((mid, match, info))

        scored.sort(key=lambda x: -x[1])

        if scored:
            best_mid, best_match, best_info = scored[0]
            assignments[series] = {
                "effort": effort,
                "src_score": src_score,
                "target": best_mid,
                "target_score": best_info["arena_score"],
                "target_quota": best_info["quota"],
                "target_usage_quota": best_info.get("usage_quota", 0),
                "target_rank": best_info["arena_rank"],
                "target_effort": best_info.get("arena_effort") or "-",
            }

    # Cheap models: route to free models first
    free_models = sorted(
        [mid for mid in opencode_models if "free" in mid],
        key=lambda m: opencode_models[m]["rp5h"],
        reverse=True
    )
    
    cheap_routing = {}
    for i, cheap_model in enumerate(CHEAP_MODELS):
        if i < len(free_models):
            cheap_routing[cheap_model] = free_models[i]
        else:
            cheap_routing[cheap_model] = highest_quota_model

    for fm in fixed_models_data:
        if fm["series"] in cheap_routing:
            target = cheap_routing[fm["series"]]
            assignments[fm["series"]] = {
                "effort": fm["effort"],
                "src_score": fm["arena_score"],
                "target": target,
                "target_score": None,
                "target_quota": opencode_models[target]["rp5h"],
                "target_usage_quota": opencode_models[target].get("usage_quota", 0),
                "target_rank": None,
                "target_effort": "-",
            }

    return assignments, fixed_models_data


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def generate_csv_output(
    assignments: Dict[str, dict],
    opencode_models: Dict[str, dict],
) -> str:
    """Generate CSV output for mapping results."""
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        "request_model",
        "target_model",
        "protocol",
        "src_score",
        "target_score",
        "RP5H",
        "price_out",
        "retention",
        "effort",
        "usage_quota",
    ])
    
    for series in CLAUDE_MODELS + GPT_MODELS + CHEAP_MODELS:
        if series not in assignments:
            continue
        a = assignments[series]
        target_id = a["target"]
        target_info = opencode_models.get(target_id, {})
        
        protocol = target_info.get("protocol", "unknown")
        price_out = target_info.get("price_output", 0)
        retention = target_info.get("retention", 0)
        rp5h = target_info.get("rp5h", 0)
        effort = a.get("target_effort", "-")
        usage_quota = a.get("target_usage_quota", 0)
        
        src_score = f"{a['src_score']:.1f}" if a.get('src_score') else "-"
        target_score = f"{a['target_score']:.1f}" if a.get('target_score') else "-"
        
        writer.writerow([
            series,
            target_id,
            protocol,
            src_score,
            target_score,
            rp5h,
            f"${price_out}",
            retention,
            effort,
            usage_quota,
        ])
    
    return output.getvalue()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Compute model mapping and output CSV')
    parser.add_argument('--output', type=str, default=None,
                        help='Output file path (default: references/mapping-{YYMMDD}.csv)')
    parser.add_argument('--stdout', action='store_true',
                        help='Also print CSV to stdout')
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    skill_dir = script_dir.parent
    repo_root = skill_dir.parent
    refs = skill_dir / "references"

    models_csv_path = repo_root / "models.csv"
    leaderboard_path = refs / "leaderboard.json"

    if not models_csv_path.exists():
        print(f"Missing: {models_csv_path}", file=sys.stderr)
        print("Check GitHub Actions workflow status", file=sys.stderr)
        sys.exit(1)

    if not leaderboard_path.exists():
        print(f"Missing: {leaderboard_path}", file=sys.stderr)
        print("Run fetch_data.py first", file=sys.stderr)
        sys.exit(1)

    opencode_models = read_csv(models_csv_path)
    arena_data = read_json(leaderboard_path)

    if not opencode_models:
        print("Error: models.csv is empty", file=sys.stderr)
        sys.exit(1)

    if not arena_data:
        print("Error: leaderboard.json is empty", file=sys.stderr)
        sys.exit(1)

    # Compute mapping
    assignments, fixed_models_data = generate_mapping(opencode_models, arena_data)

    # Generate CSV
    csv_content = generate_csv_output(assignments, opencode_models)

    # Write to file
    today = datetime.now().strftime("%y%m%d")
    output_path = args.output or (refs / f"mapping-{today}.csv")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(csv_content)
    print(f"Written: {output_path}", file=sys.stderr)

    # Print to stdout if requested
    if args.stdout:
        print(csv_content)

    # Print summary
    print(f"\nSummary: {len(assignments)} models mapped", file=sys.stderr)
    distinct_targets = len(set(a["target"] for a in assignments.values()))
    print(f"Distinct targets: {distinct_targets}", file=sys.stderr)
    for series, a in assignments.items():
        print(f"  {series:25s} -> {a['target']}", file=sys.stderr)


if __name__ == "__main__":
    main()
