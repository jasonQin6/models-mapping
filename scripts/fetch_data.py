#!/usr/bin/env python3
"""
Arena Leaderboard Fetcher — runs on Mac.
Fetches arena leaderboard data (top 100), outputs JSON cache.

Exit codes:
  0: data changed
  2: no changes
  1: error
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

EXIT_NO_CHANGES = 2
ARENA_URL = "https://lmarena.ai/leaderboard/code/webdev"


def fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> str:
    cmd = ["curl", "-sL", "--max-time", "30", url]
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr}")
    return result.stdout


def compute_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def read_hash(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    with open(path, 'r') as f:
        return f.read().strip()


def write_hash(path: Path, hash_value: str):
    with open(path, 'w') as f:
        f.write(hash_value)


def write_json(path: Path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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


def main():
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    data_dir = repo_root / "data"
    data_dir.mkdir(exist_ok=True)

    leaderboard_path = data_dir / "leaderboard.json"
    leaderboard_hash_path = data_dir / "leaderboard.hash"

    print("=" * 60)
    print("Fetching Arena WebDev leaderboard...")
    print("=" * 60)

    try:
        arena_entries = fetch_arena_leaderboard(100)
        print(f"  Found {len(arena_entries)} entries")

        new_content = json.dumps(arena_entries, sort_keys=True, ensure_ascii=False)
        new_hash = compute_hash(new_content)
        cached_hash = read_hash(leaderboard_hash_path)

        if new_hash == cached_hash:
            print("  No changes detected (hash match)")
            sys.exit(EXIT_NO_CHANGES)

        write_json(leaderboard_path, arena_entries)
        write_hash(leaderboard_hash_path, new_hash)
        print(f"  Updated {leaderboard_path.name}")
        print(f"  Hash: {new_hash[:16]}...")
        print("\nData changed. Proceed with compute_mapping.py")
        sys.exit(0)

    except Exception as e:
        print(f"  FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
