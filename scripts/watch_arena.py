#!/usr/bin/env python3
"""
Arena Leaderboard Watcher
Fetches arena leaderboard data and merges into models.csv.

This script maintains arena-related columns in models.csv:
- arena_score, arena_rank, arena_context
- organization, effort

Fallback strategies (in order):
  1. Direct match
  2. Remove "-contributor" suffix (e.g., muse-spark-1.2-contributor → muse-spark-1.2)
  3. Version downgrade (e.g., qwen3.7-plus → qwen3.6-plus)
  4. Prefix match with wildcard (e.g., claude-haiku → claude-haiku-*)
  5. Free model default (arena_score=0)
"""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

ARENA_URL = "https://lmarena.ai/leaderboard/code/webdev"

# Request models (claude/gpt) - only those NOT provided by opencode
CLAUDE_MODELS = [
    "claude-opus-5",
    "claude-fable-5",
    "claude-sonnet-5",
]

GPT_MODELS = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    # gpt-5.6-luna is provided by opencode, so excluded
    "gpt-5.5",
    "gpt-5.4",
]

CHEAP_MODELS = ["claude-haiku", "gpt-5.4-mini"]


def fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> str:
    cmd = ["curl", "-sL", "--max-time", "30", url]
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr}")
    return result.stdout


def fetch_arena_leaderboard(top_n: int = 100) -> List[dict]:
    html = fetch_url(ARENA_URL)

    idx = html.find('entries\\":[')
    if idx < 0:
        raise ValueError("Could not find entries data in Arena page")

    start = html.find('[', idx)
    depth = 0
    end = start
    for i in range(start, min(start + 500000, len(html))):
        if html[i] == '[':
            depth += 1
        elif html[i] == ']':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    json_str = html[start:end]
    json_str = json_str.replace('\\"', '"').replace('\\\\', '\\')
    entries = json.loads(json_str)

    result = []
    for entry in entries[:top_n]:
        model_name = entry.get("modelDisplayName", "")
        rank = entry.get("rank", 0)
        rating = entry.get("rating", 0)
        context = entry.get("contextLength")
        org = entry.get("modelOrganization", "")

        normalized = re.sub(r'\s*\([^)]+\)\s*$', '', model_name).strip()
        normalized = re.sub(r'-\d{8}$', '', normalized)
        normalized = re.sub(r'-\d+k$', '', normalized)

        effort = None
        for eff in {"max", "xhigh", "ultra", "high", "medium", "low"}:
            suffix = f"-{eff}"
            if normalized.lower().endswith(suffix) and not normalized.lower().startswith("qwen"):
                normalized = normalized[:-len(suffix)]
                effort = eff
                break

        normalized = normalized.lower().replace(" ", "-")

        result.append({
            "model_id": normalized,
            "effort": effort,
            "rank": rank,
            "rating": round(rating, 2),
            "context": context if context else "-",
            "organization": org,
        })

    return result


def read_csv(path: Path) -> List[dict]:
    """Read models.csv and return list of rows."""
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def build_arena_lookup(arena_data: List[dict]) -> Dict[str, dict]:
    """Build arena lookup by model_id, keeping highest rating per model."""
    lookup = {}
    for entry in arena_data:
        model_id = entry['model_id']
        if model_id not in lookup or entry['rating'] > lookup[model_id]['rating']:
            lookup[model_id] = entry
    return lookup


def try_remove_contributor(model_id: str) -> Optional[str]:
    """Try to remove -contributor suffix."""
    if model_id.endswith('-contributor'):
        return model_id[:-len('-contributor')]
    return None


def try_version_downgrade(model_id: str) -> Optional[str]:
    """Try to downgrade version number (e.g., qwen3.7-plus → qwen3.6-plus)."""
    match = re.match(r'^(.+?)(\d+)\.(\d+)(.*)$', model_id)
    if match:
        prefix = match.group(1)
        major = int(match.group(2))
        minor = int(match.group(3))
        suffix = match.group(4)
        
        if minor > 0:
            return f"{prefix}{major}.{minor - 1}{suffix}"
    
    return None


def try_prefix_match(model_id: str, arena_lookup: Dict[dict]) -> Optional[dict]:
    """Try to match by prefix (e.g., claude-haiku → claude-haiku-*)."""
    candidates = []
    for arena_id, entry in arena_lookup.items():
        if arena_id.startswith(f"{model_id}-"):
            candidates.append(entry)
    
    if candidates:
        return max(candidates, key=lambda e: e['rating'])
    
    return None


def get_arena_data_with_fallback(model_id: str, arena_lookup: Dict[dict], is_free: bool = False) -> Optional[dict]:
    """Get arena data for model_id with fallback strategies."""
    # Strategy 1: Direct match
    if model_id in arena_lookup:
        return arena_lookup[model_id]
    
    # Strategy 2: Remove -contributor suffix
    alt_id = try_remove_contributor(model_id)
    if alt_id and alt_id in arena_lookup:
        return arena_lookup[alt_id]
    
    # Strategy 3: Version downgrade
    alt_id = try_version_downgrade(model_id)
    if alt_id and alt_id in arena_lookup:
        return arena_lookup[alt_id]
    
    # Strategy 4: Prefix match with wildcard
    match = try_prefix_match(model_id, arena_lookup)
    if match:
        return match
    
    # Strategy 5: Free model default
    if is_free:
        return {
            'rank': 0,
            'rating': 0,
            'context': '-',
            'organization': 'Unknown',
            'effort': None,
        }
    
    return None


def merge_arena_fields(row: dict, arena_data: Optional[dict]) -> dict:
    """Merge arena fields into a row."""
    if arena_data:
        row['arena_rank'] = arena_data['rank']
        row['arena_score'] = arena_data['rating']
        row['arena_context'] = arena_data.get('context', '-')
        row['organization'] = arena_data.get('organization', '')
        row['effort'] = arena_data.get('effort') or '-'
    else:
        row['arena_rank'] = ''
        row['arena_score'] = ''
        row['arena_context'] = '-'
        row['organization'] = ''
        row['effort'] = '-'
    
    return row


def create_request_row(model_id: str, arena_data: Optional[dict]) -> dict:
    """Create a request model row (claude/gpt)."""
    row = {
        'model_id': model_id,
        'protocol': '',
        'rp5h': '',
        'usage_quota': '',
        'price_output': '',
        'max_price_output': '',
        'rpw': '',
        'rpm': '',
        'price_input': '',
        'price_cached_read': '',
        'price_cached_write': '',
        'context_threshold': '-',
        'peak_hours': '-',
        'retention': '',
        'provider': arena_data.get('organization', '') if arena_data else '',
        'mapping': '',
    }
    
    return merge_arena_fields(row, arena_data)


def main():
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    
    models_csv_path = repo_root / 'models.csv'
    
    # Read existing models.csv
    opencode_rows = read_csv(models_csv_path)
    
    if not opencode_rows:
        print('Error: models.csv is empty', file=sys.stderr)
        sys.exit(1)
    
    # Fetch arena data
    print("Fetching Arena WebDev leaderboard...", file=sys.stderr)
    arena_data = fetch_arena_leaderboard(100)
    print(f"Found {len(arena_data)} entries", file=sys.stderr)
    
    # Build arena lookup
    arena_lookup = build_arena_lookup(arena_data)
    
    # Get list of opencode model_ids
    opencode_model_ids = {row['model_id'] for row in opencode_rows}
    
    # Merge arena data into opencode rows
    all_rows = []
    for row in opencode_rows:
        model_id = row['model_id']
        is_free = 'free' in model_id.lower()
        arena_entry = get_arena_data_with_fallback(model_id, arena_lookup, is_free)
        
        # Set provider to "opencode" for all opencode models
        row['provider'] = 'opencode'
        
        # Merge arena fields
        row = merge_arena_fields(row, arena_entry)
        row['mapping'] = ''  # Opencode models don't have mapping
        all_rows.append(row)
    
    # Add request models (claude/gpt) - only those NOT in opencode
    request_models = CLAUDE_MODELS + GPT_MODELS + CHEAP_MODELS
    for model_id in request_models:
        if model_id in opencode_model_ids:
            continue
        arena_entry = get_arena_data_with_fallback(model_id, arena_lookup)
        row = create_request_row(model_id, arena_entry)
        all_rows.append(row)
    
    # Sort by arena_score descending
    def sort_key(row):
        score = row.get('arena_score', '')
        if score == '' or score is None:
            return -1
        try:
            return float(score)
        except (ValueError, TypeError):
            return -1
    
    all_rows.sort(key=sort_key, reverse=True)
    
    # Write output
    fieldnames = [
        'model_id', 'provider', 'protocol',
        'arena_score', 'arena_rank', 'rp5h', 'usage_quota', 'price_output', 'max_price_output',
        'rpw', 'rpm', 'price_input', 'price_cached_read', 'price_cached_write',
        'context_threshold', 'peak_hours',
        'retention',
        'arena_context', 'organization', 'effort',
        'mapping',
    ]
    
    with open(models_csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    
    opencode_count = len(opencode_rows)
    request_count = len(all_rows) - opencode_count
    print(f'Merged {opencode_count} opencode models + {request_count} request models', file=sys.stderr)
    print(f'Written: {models_csv_path}', file=sys.stderr)


if __name__ == "__main__":
    main()
