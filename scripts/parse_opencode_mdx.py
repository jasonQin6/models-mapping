#!/usr/bin/env python3
"""
Parse opencode go.mdx from GitHub and output models.csv.

Extracts data from four markdown tables:
1. Usage limits (rp5h, rpw, rpm)
2. Pricing (input, output, cached_read, cached_write, usage_quota)
3. Endpoints (model_id, endpoint -> protocol)
4. Privacy (retention, model_training)
"""

import csv
import io
import re
import sys
from typing import Dict, List, Optional, Tuple


def normalize_name(name: str) -> str:
    """Normalize model name for matching across tables.
    
    Strips parenthetical notes, lowercases, replaces spaces with hyphens.
    This ensures 'MiMo V2.5' and 'MiMo-V2.5' match to the same key.
    """
    # Remove parenthetical notes like "(≤ 272K tokens)" or "(Off-Peak)"
    name = re.sub(r'\s*\([^)]+\)', '', name)
    # Remove trailing notes like "(limited time)" or "(limited regions)"
    name = re.sub(r'\s*\(.*$', '', name)
    # Normalize: lowercase, replace spaces with hyphens, collapse multiple hyphens
    name = name.strip().lower().replace(' ', '-')
    name = re.sub(r'-+', '-', name)
    return name


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


def parse_retention(value: str) -> Tuple[int, str]:
    value = value.strip()
    if 'Not ZDR' in value or 'not zdr' in value.lower():
        return (0, 'Not ZDR')
    match = re.search(r'(\d+)', value)
    if match:
        days = int(match.group(1))
        note = 'See footnote' if '*' in value else ''
        return (days, note)
    return (0, value)


def find_tables_in_text(text: str) -> List[List[str]]:
    """Find all markdown tables in text, return as list of table line lists."""
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

    # 1. Usage limits section: rp5h/rpw/rpm table + pricing table
    usage_section = extract_section(content, 'Usage limits')
    if usage_section:
        tables = find_tables_in_text(usage_section)
        
        # First table: rp5h, rpw, rpm
        if tables:
            rows = parse_table_lines(tables[0])
            for row in rows:
                raw_name = row.get('Model', '').strip()
                key = normalize_name(raw_name)
                if not key:
                    continue
                models[key] = {
                    'name': raw_name,
                    'rp5h': parse_int_or_none(row.get('requests per 5 hour', '-')),
                    'rpw': parse_int_or_none(row.get('requests per week', '-')),
                    'rpm': parse_int_or_none(row.get('requests per month', '-')),
                }
        
        # Second table: pricing
        if len(tables) >= 2:
            rows = parse_table_lines(tables[1])
            for row in rows:
                raw_name = row.get('Model', '').strip()
                key = normalize_name(raw_name)
                if not key:
                    continue
                # Skip duplicate entries (Peak/Off-Peak, context-length variants)
                if key in models and 'usage_quota' in models[key] and models[key]['usage_quota'] is not None:
                    continue
                if key not in models:
                    models[key] = {'name': raw_name}
                models[key] = merge_model(models[key], {
                    'price_input': parse_price(row.get('Input', '-')),
                    'price_output': parse_price(row.get('Output', '-')),
                    'price_cached_read': parse_price(row.get('Cached Read', '-')),
                    'price_cached_write': parse_price(row.get('Cached Write', '-')),
                    'usage_quota': parse_price(row.get('Usage', '-')),
                })

    # 2. Endpoints table
    endpoints_section = extract_section(content, 'Endpoints')
    if endpoints_section:
        tables = find_tables_in_text(endpoints_section)
        if tables:
            rows = parse_table_lines(tables[0])
            for row in rows:
                raw_name = row.get('Model', '').strip()
                key = normalize_name(raw_name)
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

    # 3. Privacy table
    privacy_section = extract_section(content, 'Privacy')
    if privacy_section:
        tables = find_tables_in_text(privacy_section)
        if tables:
            rows = parse_table_lines(tables[0])
            for row in rows:
                raw_name = row.get('Model', '').strip()
                key = normalize_name(raw_name)
                if not key:
                    continue
                retention_str = row.get('Data retention', '0 days')
                retention_days, retention_note = parse_retention(retention_str)
                model_training = row.get('Model training', 'Not used').strip()
                if key not in models:
                    models[key] = {'name': raw_name}
                models[key] = merge_model(models[key], {
                    'retention': retention_days,
                    'retention_note': retention_note,
                    'model_training': model_training,
                })

    return models


def generate_csv(models: Dict[str, dict]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'model_id', 'name', 'protocol',
        'rp5h', 'rpw', 'rpm',
        'usage_quota', 'price_input', 'price_output',
        'price_cached_read', 'price_cached_write',
        'retention', 'retention_note', 'model_training',
    ])
    sorted_models = sorted(models.values(), key=lambda m: m.get('model_id', m.get('name', '')))
    for model in sorted_models:
        writer.writerow([
            model.get('model_id', ''),
            model.get('name', ''),
            model.get('protocol', ''),
            model.get('rp5h', ''),
            model.get('rpw', ''),
            model.get('rpm', ''),
            model.get('usage_quota', ''),
            model.get('price_input', ''),
            model.get('price_output', ''),
            model.get('price_cached_read', ''),
            model.get('price_cached_write', ''),
            model.get('retention', ''),
            model.get('retention_note', ''),
            model.get('model_training', ''),
        ])
    return output.getvalue()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Parse opencode go.mdx')
    parser.add_argument('input', nargs='?', help='Input .mdx file (default: stdin)')
    parser.add_argument('--output', '-o', help='Output CSV file')
    args = parser.parse_args()

    if args.input:
        with open(args.input, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        content = sys.stdin.read()

    models = parse_mdx(content)
    if not models:
        print('Error: No models parsed', file=sys.stderr)
        sys.exit(1)

    csv_content = generate_csv(models)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(csv_content)
        print(f'Written: {args.output} ({len(models)} models)', file=sys.stderr)
    else:
        print(csv_content)
        print(f'Parsed {len(models)} models', file=sys.stderr)


if __name__ == '__main__':
    main()
