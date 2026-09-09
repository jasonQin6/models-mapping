#!/usr/bin/env python3
"""Fetch the OpenCode Go document and update ``data/models_extra.json``.

``watch-go`` is the source watcher for the opencode-go channel.  The
``go.mdx`` document is the channel's model-list fact source: the channel
section's keys are exactly the go.mdx model ids, and an id that leaves the
document leaves the section (ADR 0011).  The section carries channel-declared
facts only (quotas, prices); public card data is filled at
planning time from ``data/all_models.json``, never here.

Channel-provided ``claude-*`` models are not collected (ADR 0012).  The
parser transcribes declarations only — the document's zero prices are kept
as-is, and free quotas are re-derived at planning time from the model's
owning channel so the rule lives in one place.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from error_state import clear_error, dump_page, persist_error  # noqa: E402
from models_extra import (  # noqa: E402
    DEFAULT_EXTRA_PATH,
    is_excluded_model,
    update_channel,
)


GO_MDX_URL = (
    "https://raw.githubusercontent.com/anomalyco/opencode/"
    "dev/packages/web/src/content/docs/go.mdx"
)
GO_COMMIT_API_URL = (
    "https://api.github.com/repos/anomalyco/opencode/commits"
    "?path=packages/web/src/content/docs/go.mdx&sha=dev&per_page=1"
)
CHANNEL = "go"


def fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch text over HTTP using only the Python standard library."""

    request = Request(url, headers={"User-Agent": "models-mapping/watch-go"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def fetch_latest_commit(url: str = GO_COMMIT_API_URL) -> Optional[str]:
    """Return the latest source commit, or ``None`` when unavailable."""

    try:
        payload = json.loads(fetch_url(url))
    except (OSError, ValueError):
        return None

    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            commit = first.get("sha")
            if commit:
                return str(commit)
    return None


def _json_value(value: Any) -> Any:
    """Convert parser placeholder values to stable JSON values."""

    if value in ("", "-"):
        return None
    return value


# --------------------------------------------------------------------------
# go.mdx parsing (single-consumer: this watcher is the only user)


def normalize_model_key(name: str) -> str:
    """Derive the channel key from an mdx table's Model cell.

    Collection-side keying for the go.mdx tables (lowercase, spaces to
    hyphens, parentheticals dropped, hyphens collapsed).  Kept local on
    purpose: the planning layer's ``name_matching`` serves arena/registry
    matching, and the collection layer does not import across layers.  The
    transformation must stay byte-compatible with historical snapshot keys.
    """

    value = re.sub(r'\s*\([^)]+\)', '', name)
    value = value.strip().lower().replace(' ', '-')
    return re.sub(r'-+', '-', value)


def parse_price(value: str) -> Optional[float]:
    value = value.strip()
    if value in ('-', '', 'N/A'):
        return None
    value = value.replace('$', '').replace(',', '').strip()
    try:
        return float(value)
    except ValueError:
        return None


def parse_int_or_none(value: str) -> Optional[int]:
    value = value.strip()
    if value in ('-', '', 'N/A'):
        return None
    value = value.replace(',', '').strip()
    try:
        return int(value)
    except ValueError:
        return None


def find_tables_in_text(text: str) -> List[List[str]]:
    """Find all markdown tables in text."""
    lines = text.split('\n')
    tables = []
    current_table = []
    for line in lines:
        if '|' in line and line.strip().startswith('|'):
            current_table.append(line)
        else:
            if current_table:
                tables.append(current_table)
                current_table = []
    if current_table:
        tables.append(current_table)
    return tables


def parse_table_lines(table_lines: List[str]) -> List[dict]:
    """Parse table lines into list of dicts."""
    if len(table_lines) < 2:
        return []
    header_line = table_lines[0]
    headers = [h.strip() for h in header_line.split('|')[1:-1]]
    data_start = 1
    if len(table_lines) > 1 and '---' in table_lines[1]:
        data_start = 2
    rows = []
    for i in range(data_start, len(table_lines)):
        line = table_lines[i]
        if '---' in line:
            continue
        cells = [c.strip() for c in line.split('|')[1:-1]]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows


def extract_section(text: str, heading: str) -> Optional[str]:
    pattern = rf'^#{{1,3}}\s+{re.escape(heading)}.*?\n(.*?)(?=^#{{1,3}}\s|\Z)'
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1)
    return None


def merge_model(existing: dict, new_data: dict) -> dict:
    """Merge new_data into existing, only overwriting if new value is not None/empty."""
    result = dict(existing)
    for key, value in new_data.items():
        if value is not None and value != '':
            result[key] = value
    return result


def parse_mdx(content: str) -> Dict[str, dict]:
    """Parse the go.mdx tables into raw model records.

    Reads three tables — usage limits (rp5h), pricing (the four prices plus
    usage_quota) and endpoints.  Variant rows of one model in the pricing
    table collapse to the row with the cheapest output price.  The parser
    transcribes declarations only: undeclared cells stay ``None`` and no
    value is derived from other rows — free-model backfill and any other
    cleaning belong to the planning layer (models_mapping.py).
    """

    models = {}

    # 1. Usage limits section
    usage_section = extract_section(content, 'Usage limits')
    if usage_section:
        tables = find_tables_in_text(usage_section)

        # First table: rp5h
        if tables:
            rows = parse_table_lines(tables[0])
            for row in rows:
                raw_name = row.get('Model', '').strip()
                key = normalize_model_key(raw_name)
                if not key:
                    continue
                models[key] = {
                    'name': raw_name,
                    'rp5h': parse_int_or_none(row.get('requests per 5 hour', '-')),
                }

        # Second table: pricing.  Variant rows of one model (conditions in
        # the Model cell) collapse to the row with the cheapest output price.
        if len(tables) >= 2:
            rows = parse_table_lines(tables[1])
            cheapest: Dict[str, dict] = {}
            for row in rows:
                raw_name = row.get('Model', '').strip()
                key = normalize_model_key(raw_name)
                if not key:
                    continue
                record = {
                    'name': re.sub(r'\s*\([^)]+\)', '', raw_name).strip() or raw_name,
                    'price_input': parse_price(row.get('Input', '-')),
                    'price_output': parse_price(row.get('Output', '-')),
                    'price_cached_read': parse_price(row.get('Cached Read', '-')),
                    'price_cached_write': parse_price(row.get('Cached Write', '-')),
                    'usage_quota': parse_price(row.get('Usage', '-')),
                }
                if record['price_output'] is None:
                    continue
                current = cheapest.get(key)
                if current is None or record['price_output'] < current['price_output']:
                    cheapest[key] = record

            for key, record in cheapest.items():
                name = record.pop('name')
                if key not in models:
                    models[key] = {'name': name}
                models[key] = merge_model(models[key], record)

    # 2. Endpoints table
    endpoints_section = extract_section(content, 'Endpoints')
    if endpoints_section:
        tables = find_tables_in_text(endpoints_section)
        if tables:
            rows = parse_table_lines(tables[0])
            for row in rows:
                raw_name = row.get('Model', '').strip()
                key = normalize_model_key(raw_name)
                if not key:
                    continue
                model_id = row.get('Model ID', '').strip()
                endpoint = row.get('Endpoint', '').strip()
                protocol = 'unknown'
                if '/responses' in endpoint:
                    protocol = 'responses'
                elif '/messages' in endpoint:
                    protocol = 'messages'
                elif '/completions' in endpoint:
                    protocol = 'completions'
                if key not in models:
                    models[key] = {'name': raw_name}
                models[key] = merge_model(models[key], {
                    'model_id': model_id,
                    'endpoint': endpoint,
                    'protocol': protocol,
                })

    return models


def build_go_section(content: str) -> Dict[str, dict]:
    """Parse go.mdx into the opencode-go section of models_extra.json.

    Keys are exactly the go.mdx model ids (claude-* dropped); values carry
    channel-declared facts only: display name, quotas, and the four prices
    assembled into ``cost`` (``cache_*`` keys align with models.dev and the
    goat section).
    """

    parsed = parse_mdx(content)
    models: Dict[str, dict] = {}
    for key, source in parsed.items():
        model_id = str(source.get("model_id") or key).strip()
        if not model_id or is_excluded_model(model_id):
            continue
        record = {
            "name": _json_value(source.get("name")) or model_id,
            "rp5h": _json_value(source.get("rp5h")),
            "usage_quota": _json_value(source.get("usage_quota")),
            "cost": {
                "input": _json_value(source.get("price_input")),
                "output": _json_value(source.get("price_output")),
                "cache_read": _json_value(source.get("price_cached_read")),
                "cache_write": _json_value(source.get("price_cached_write")),
            },
        }
        models[model_id] = record
    return models


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update the opencode-go section of data/models_extra.json from go.mdx"
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Local go.mdx file; omit to fetch the upstream document",
    )
    parser.add_argument("--url", default=GO_MDX_URL, help="go.mdx source URL")
    parser.add_argument(
        "--source-commit",
        help="Source commit (stored nowhere; for logging only)",
    )
    parser.add_argument(
        "--extra",
        type=Path,
        default=DEFAULT_EXTRA_PATH,
        help="Path to data/models_extra.json (default: data/models_extra.json)",
    )
    args = parser.parse_args(argv)

    content = ""
    try:
        if args.input:
            content = Path(args.input).read_text(encoding="utf-8")
        else:
            content = fetch_url(args.url)
        models = build_go_section(content)
        if not models:
            raise ValueError("no models found in go.mdx")
        update_channel(args.extra, "opencode-go", models)
    except (OSError, ValueError) as exc:
        dump = dump_page(CHANNEL, content) if "content" in locals() else None
        persist_error(CHANNEL, "watch_go.py", str(exc), dump)
        print(f"watch-go: {exc}", file=sys.stderr)
        return 1

    clear_error(CHANNEL)
    print(f"watch-go: {len(models)} Go models -> {args.extra}")
    if args.source_commit:
        print(f"watch-go: source commit {args.source_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
