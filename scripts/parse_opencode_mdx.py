#!/usr/bin/env python3
"""
Parse opencode go.mdx from GitHub and output models.csv.

Extracts data from four markdown tables:
1. Usage limits (rp5h, rpw, rpm)
2. Pricing (input, output, cached_read, cached_write, usage_quota)
3. Endpoints (model_id, endpoint -> protocol)
4. Privacy (retention, model_training)

Handles pricing variants (context length, peak/off-peak) by keeping the
cheapest output price as base and recording max_price_output.
"""

import csv
import io
import re
import sys
from typing import Dict, List, Optional, Tuple


def normalize_name(name: str) -> str:
    """Normalize model name for matching across tables.
    
    Strips parenthetical notes, lowercases, replaces spaces with hyphens.
    """
    name = re.sub(r'\s*\([^)]+\)', '', name)
    name = re.sub(r'\s*\(.*$', '', name)
    name = name.strip().lower().replace(' ', '-')
    name = re.sub(r'-+', '-', name)
    return name


def extract_variant_condition(name: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Extract variant condition from model name.
    
    Returns (base_name, context_threshold, peak_hours).
    Examples:
        "GPT 5.6 Luna (≤ 272K tokens)" -> ("GPT 5.6 Luna", "272K", None)
        "DeepSeek V4 Pro (Peak)" -> ("DeepSeek V4 Pro", None, "peak")
    """
    match = re.search(r'\(([^)]+)\)', name)
    base_name = re.sub(r'\s*\([^)]+\)', '', name).strip()
    
    if not match:
        return (base_name, None, None)
    
    condition = match.group(1).strip()
    
    # Context length variants: "≤ 272K tokens" or "> 272K tokens"
    context_match = re.search(r'[≤<>=]\s*(\d+K?)', condition, re.IGNORECASE)
    if context_match and 'token' in condition.lower():
        threshold = context_match.group(1).upper()
        if not threshold.endswith('K'):
            threshold += 'K'
        return (base_name, threshold, None)
    
    # Time-based variants: "Off-Peak" or "Peak"
    if 'peak' in condition.lower():
        if 'off-peak' in condition.lower():
            return (base_name, None, 'off-peak')
        else:
            return (base_name, None, 'peak')
    
    return (base_name, None, None)


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


def extract_peak_hours(text: str) -> Optional[str]:
    """Extract peak hours from document text."""
    # Look for pattern like "Peak hours are 01:00-04:00 and 06:00-10:00 UTC"
    match = re.search(r'Peak hours? are ([^.;]+(?:UTC|utc))', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
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
    
    # Extract peak hours for DeepSeek models
    peak_hours = extract_peak_hours(content)

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
        
        # Second table: pricing (with variants)
        if len(tables) >= 2:
            rows = parse_table_lines(tables[1])
            
            # Group by base model
            model_variants = {}
            for row in rows:
                raw_name = row.get('Model', '').strip()
                base_name, context_thresh, peak_type = extract_variant_condition(raw_name)
                key = normalize_name(base_name)
                
                if not key:
                    continue
                
                if key not in model_variants:
                    model_variants[key] = []
                
                model_variants[key].append({
                    'raw_name': raw_name,
                    'base_name': base_name,
                    'context_threshold': context_thresh,
                    'peak_type': peak_type,
                    'price_input': parse_price(row.get('Input', '-')),
                    'price_output': parse_price(row.get('Output', '-')),
                    'price_cached_read': parse_price(row.get('Cached Read', '-')),
                    'price_cached_write': parse_price(row.get('Cached Write', '-')),
                    'usage_quota': parse_price(row.get('Usage', '-')),
                })
            
            # Process each model's variants
            for key, variants in model_variants.items():
                # Find variant with lowest output price (base)
                valid_variants = [v for v in variants if v['price_output'] is not None]
                if not valid_variants:
                    continue
                
                base_variant = min(valid_variants, key=lambda v: v['price_output'])
                max_output = max(v['price_output'] for v in valid_variants)
                
                # Determine context_threshold and peak_hours
                context_threshold = None
                peak_hours_value = None
                
                for v in variants:
                    if v['context_threshold'] and v['price_output'] == base_variant['price_output']:
                        context_threshold = v['context_threshold']
                    if v['peak_type'] == 'off-peak':
                        peak_hours_value = peak_hours  # Use extracted peak hours
                
                # For models with peak/off-peak, record peak_hours
                has_peak_variant = any(v['peak_type'] == 'peak' for v in variants)
                if has_peak_variant and peak_hours:
                    peak_hours_value = peak_hours
                
                if key not in models:
                    models[key] = {'name': base_variant['base_name']}
                
                models[key] = merge_model(models[key], {
                    'price_input': base_variant['price_input'],
                    'price_output': base_variant['price_output'],
                    'price_cached_read': base_variant['price_cached_read'],
                    'price_cached_write': base_variant['price_cached_write'],
                    'usage_quota': base_variant['usage_quota'],
                    'max_price_output': max_output,
                    'context_threshold': context_threshold or '-',
                    'peak_hours': peak_hours_value or '-',
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
        'usage_quota', 'price_input', 'price_output', 'max_price_output',
        'price_cached_read', 'price_cached_write',
        'context_threshold', 'peak_hours',
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
            model.get('max_price_output', ''),
            model.get('price_cached_read', ''),
            model.get('price_cached_write', ''),
            model.get('context_threshold', '-'),
            model.get('peak_hours', '-'),
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
