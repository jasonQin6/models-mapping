#!/usr/bin/env python3
"""
AxonHub Data Fetcher — runs on Mac.
Fetches from external sources, outputs JSON cache.

Default: fetch opencode, if changed then fetch arena.
--skip hash: skip hash detection, force fetch both.
--skip fetch: skip all fetching, exit 0 (use existing cache for compute_mapping).
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

EXIT_NO_CHANGES = 2

ARENA_URL = "https://lmarena.ai/leaderboard/code/webdev"
OPENCODE_URL = "https://opencode.ai/docs/zh-cn/go/"


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> str:
    cmd = ["curl", "-sL", "--max-time", "30", url]
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr}")
    return result.stdout


# ---------------------------------------------------------------------------
# Hash-based change detection
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------

def write_json(path: Path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def parse_numeric(s: str) -> float:
    s = s.strip().replace('$', '').replace(',', '').strip()
    if s in ('-', '', '免费'):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_retention(s: str) -> int:
    s = s.strip()
    if '非 ZDR' in s or 'non-ZDR' in s.lower():
        return 365
    match = re.search(r'(\d+)', s)
    if match:
        return int(match.group(1))
    return 0


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_opencode_models() -> Dict[str, dict]:
    html = fetch_url(OPENCODE_URL)
    models = {}

    name_pattern = r'<li>\s*<strong>([^<]+)</strong>\s*(?:\([^)]+\))?\s*</li>'
    name_matches = re.findall(name_pattern, html)

    rate_pattern = r'<tr>\s*<td>([^<]+)</td>\s*<td>([\d,]+)</td>\s*<td>([\d,]+)</td>\s*<td>([\d,]+)</td>'
    rate_matches = re.findall(rate_pattern, html)
    rate_map = {}
    for name, per_5h, per_week, per_month in rate_matches:
        rate_map[name.strip()] = {
            "rp5h": int(per_5h.replace(",", "")),
            "rpw": int(per_week.replace(",", "")),
            "rpm": int(per_month.replace(",", "")),
        }

    price_pattern = r'<tr>\s*<td>([^<]+)</td>\s*<td>[^<]*</td>\s*<td>([^<]*)</td>\s*<td>[^<]*</td>\s*<td>[^<]*</td>\s*<td>([^<]*)</td>'
    price_matches = re.findall(price_pattern, html)
    price_map = {}
    for name, output_str, quota_str in price_matches:
        name = name.strip()
        normalized = name.lower().replace(' ', '-')
        price_map[normalized] = {
            "price_out": parse_numeric(output_str),
            "usage_quota": int(parse_numeric(quota_str)),
        }

    endpoint_pattern = r'<tr>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td><code[^>]*>([^<]+)</code></td>'
    endpoint_matches = re.findall(endpoint_pattern, html)
    protocol_map = {}
    for name, model_id, url in endpoint_matches:
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        protocol = path.split('/')[-1] if path else "unknown"
        protocol_map[model_id.strip()] = protocol

    retention_map = {}
    privacy_idx = html.find('数据留存')
    if privacy_idx > 0:
        table_start = html.find('<tbody>', privacy_idx)
        table_end = html.find('</table>', privacy_idx)
        if table_start > 0 and table_end > 0:
            table_html = html[table_start:table_end]
            ret_pattern = r'<tr>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>'
            for match in re.finditer(ret_pattern, table_html):
                name = match.group(1).strip()
                retention = match.group(3).strip()
                normalized = name.lower().replace(' ', '-')
                retention_map[normalized] = parse_retention(retention)

    for name in name_matches:
        name = name.strip()
        model_id = name.lower().replace(" ", "-")
        rates = rate_map.get(name, {"rp5h": 0, "rpw": 0, "rpm": 0})
        pricing = price_map.get(model_id, {"price_out": 0, "usage_quota": 0})
        protocol = protocol_map.get(model_id, "unknown")
        retention = retention_map.get(model_id, 0)

        models[model_id] = {
            "name": name,
            "protocol": protocol,
            "rp5h": rates["rp5h"],
            "rpw": rates["rpw"],
            "rpm": rates["rpm"],
            "usage_quota": pricing["usage_quota"],
            "price_out": pricing["price_out"],
            "retention": retention,
        }

    if models:
        highest = max(models.values(), key=lambda m: m["rp5h"])
        for model_id, model in models.items():
            if "free" in model_id and model["rp5h"] == 0:
                model["rp5h"] = highest["rp5h"]
                model["rpw"] = highest["rpw"]
                model["rpm"] = highest["rpm"]

    return models


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fetch AxonHub data sources')
    parser.add_argument('--skip', choices=['hash', 'fetch'],
                        help='hash: skip hash detection, force fetch both. fetch: skip all fetching, use existing cache.')
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    skill_dir = script_dir.parent
    refs = skill_dir / "references"
    refs.mkdir(exist_ok=True)

    opencode_path = refs / "opencode-list.json"
    opencode_hash_path = refs / "opencode-list.hash"
    leaderboard_path = refs / "leaderboard.json"
    leaderboard_hash_path = refs / "leaderboard.hash"

    # --skip fetch: don't fetch anything, just exit 0
    if args.skip == 'fetch':
        print("=" * 60)
        print("Skipping all fetching (--skip fetch)")
        print("Using existing cache for compute_mapping.py")
        print("=" * 60)
        sys.exit(0)

    opencode_changed = False
    leaderboard_changed = False

    # Stage 1: OpenCode
    print("=" * 60)
    print("Stage 1: Fetching OpenCode Go models...")
    print("=" * 60)

    try:
        opencode_models = fetch_opencode_models()
        print(f"  Found {len(opencode_models)} models")

        new_content = json.dumps(opencode_models, sort_keys=True, ensure_ascii=False)
        new_hash = compute_hash(new_content)
        cached_hash = read_hash(opencode_hash_path)

        if args.skip != 'hash' and new_hash == cached_hash:
            print("  No changes detected (hash match)")
        else:
            write_json(opencode_path, opencode_models)
            write_hash(opencode_hash_path, new_hash)
            print(f"  Updated {opencode_path.name}")
            print(f"  Hash: {new_hash[:16]}...")
            opencode_changed = True
    except Exception as e:
        print(f"  FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    # Stage 2: Arena (only if opencode changed or --skip hash)
    should_fetch_arena = opencode_changed or args.skip == 'hash'

    if should_fetch_arena:
        print()
        print("=" * 60)
        print("Stage 2: Fetching Arena WebDev leaderboard...")
        print("=" * 60)

        try:
            arena_entries = fetch_arena_leaderboard(100)
            print(f"  Found {len(arena_entries)} entries")

            new_content = json.dumps(arena_entries, sort_keys=True, ensure_ascii=False)
            new_hash = compute_hash(new_content)
            cached_hash = read_hash(leaderboard_hash_path)

            if args.skip != 'hash' and new_hash == cached_hash:
                print("  No changes detected (hash match)")
            else:
                write_json(leaderboard_path, arena_entries)
                write_hash(leaderboard_hash_path, new_hash)
                print(f"  Updated {leaderboard_path.name}")
                print(f"  Hash: {new_hash[:16]}...")
                leaderboard_changed = True
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print()
        print("=" * 60)
        print("Stage 2: Skipped (opencode unchanged)")
        print("=" * 60)

    # Report
    print()
    print("=" * 60)
    if not opencode_changed and not leaderboard_changed:
        print("NO_CHANGES: data identical to previous fetch.")
        sys.exit(EXIT_NO_CHANGES)
    else:
        changes = []
        if opencode_changed:
            changes.append("opencode-list.json")
        if leaderboard_changed:
            changes.append("leaderboard.json")
        print(f"Data changed: {', '.join(changes)}")
        print("Proceed with compute_mapping.py")


if __name__ == "__main__":
    main()
