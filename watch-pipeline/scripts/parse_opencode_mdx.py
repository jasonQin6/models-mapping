#!/usr/bin/env python3
"""
Parse the OpenCode Go markdown source into model records.

Extracts data from three markdown tables:
1. Usage limits (rp5h)
2. Pricing (input, output, cached_read, cached_write, usage_quota)
3. Endpoints (model_id, endpoint -> protocol)

Pricing variants (several condition rows for one model) collapse to the row
with the cheapest output price.

The parser transcribes declarations only: undeclared cells stay None and no
value is derived from other rows — free-model backfill and any other
cleaning belong to the planning layer (models_mapping.py).
"""

import re
from typing import Dict, List, Optional


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
    models = {}

    # 1. Usage limits section
    usage_section = extract_section(content, 'Usage limits')
    if usage_section:
        tables = find_tables_in_text(usage_section)
        
        # First table: rp5h, rpw, rpm
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
