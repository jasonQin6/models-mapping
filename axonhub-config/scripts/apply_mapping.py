#!/usr/bin/env python3
"""
Apply model mapping from CSV to AxonHub.

Reads a mapping CSV file (produced by models-mapping skill) and:
1. Creates/updates YYMMDD API key profile template with modelMappings
2. Configures channel associations for mapped models
3. Cleans up unused old templates

Usage:
    python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT>
    python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT> --dry-run
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

from common import fetch_graphql, DEFAULT_CHANNEL_ID


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def parse_mapping_csv(path: Path) -> Dict[str, str]:
    """Parse mapping CSV → {request_model: target_model}."""
    if not path.exists():
        raise FileNotFoundError(f"Mapping file not found: {path}")

    mapping = {}
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            req = row.get('request_model', '').strip()
            tgt = row.get('target_model', '').strip()
            if req and tgt:
                mapping[req] = tgt
    return mapping


# ---------------------------------------------------------------------------
# Template operations
# ---------------------------------------------------------------------------

def create_or_update_template(
    axonhub_url: str, token: str, mapping: Dict[str, str], dry_run: bool = False,
) -> Tuple[str, str]:
    """Create or update YYMMDD template. Returns (template_id, action)."""
    today = datetime.now().strftime("%y%m%d")

    profile = {
        "name": today,
        "modelMappings": [{"from": k, "to": v} for k, v in mapping.items()],
        "loadBalanceStrategy": "default",
        "traceStickyMode": "prefer_previous_channel",
        "channelTagsMatchMode": "any",
    }

    if dry_run:
        print(f"  [DRY RUN] Would create/update template '{today}'")
        print(f"  Mappings: {len(mapping)} models")
        return "dry-run", "dry-run"

    # Check if template already exists
    query = """
    query($name: String!) {
      apiKeyProfileTemplates(first: 100, filter: {name: $name}) {
        edges { node { id name } }
      }
    }
    """
    result = fetch_graphql(axonhub_url, token, query, {"name": today})
    existing = [e["node"] for e in result.get("data", {}).get("apiKeyProfileTemplates", {}).get("edges", [])]

    if existing:
        template_id = existing[0]["id"]
        mutation = """
        mutation($id: ID!, $profile: JSON!) {
          updateApiKeyProfileTemplate(id: $id, profile: $profile) { id name }
        }
        """
        fetch_graphql(axonhub_url, token, mutation, {"id": template_id, "profile": json.dumps(profile)})
        return template_id, "updated"
    else:
        mutation = """
        mutation($name: String!, $profile: JSON!) {
          createApiKeyProfileTemplate(name: $name, profile: $profile) { id name }
        }
        """
        result = fetch_graphql(axonhub_url, token, mutation, {"name": today, "profile": json.dumps(profile)})
        template_id = result.get("data", {}).get("createApiKeyProfileTemplate", {}).get("id")
        return template_id, "created"


# ---------------------------------------------------------------------------
# Association operations
# ---------------------------------------------------------------------------

def get_axonhub_model_ids(axonhub_url: str, token: str) -> Dict[str, str]:
    """Get {modelID: internal_id} mapping from AxonHub."""
    query = """
    query {
      models(first: 100) {
        edges { node { id modelID } }
      }
    }
    """
    result = fetch_graphql(axonhub_url, token, query)
    edges = result.get("data", {}).get("models", {}).get("edges", [])
    return {e["node"]["modelID"]: e["node"]["id"] for e in edges}


def update_associations(
    axonhub_url: str,
    token: str,
    mapping: Dict[str, str],
    model_ids: Dict[str, str],
    channel_id: int,
    dry_run: bool = False,
):
    """Update channel associations for mapped models."""
    mutation = """
    mutation UpdateModel($id: ID!, $input: UpdateModelInput!) {
      updateModel(id: $id, input: $input) {
        id modelID
        settings { associations { type priority channelModel { channelId modelId } } }
      }
    }
    """

    success = 0
    errors = 0

    for request_model, target_model in mapping.items():
        axonhub_id = model_ids.get(request_model)
        if not axonhub_id:
            print(f"  SKIP: {request_model} not found in AxonHub")
            continue

        associations = [{
            "type": "channel_model",
            "priority": 0,
            "disabled": False,
            "channelModel": {
                "channelId": channel_id,
                "modelId": target_model,
            },
        }]

        if dry_run:
            print(f"  {request_model:25s} -> ch{channel_id}:{target_model}")
            success += 1
            continue

        result = fetch_graphql(axonhub_url, token, mutation, {
            "id": axonhub_id,
            "input": {"settings": {"associations": associations}},
        })

        if "errors" in result:
            print(f"  ERROR: {request_model} - {result['errors']}")
            errors += 1
        else:
            print(f"  OK: {request_model:25s} -> ch{channel_id}:{target_model}")
            success += 1

    return success, errors


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_old_templates(axonhub_url: str, token: str, dry_run: bool = False):
    """Delete unreferenced YYMMDD templates."""
    query = """
    query {
      apiKeyProfileTemplates(first: 100) {
        edges { node { id name } }
      }
    }
    """
    result = fetch_graphql(axonhub_url, token, query)
    templates = [e["node"] for e in result.get("data", {}).get("apiKeyProfileTemplates", {}).get("edges", [])]

    today = datetime.now().strftime("%y%m%d")
    yymmdd_re = re.compile(r'^\d{6}$')
    yymmdd_templates = [t for t in templates if yymmdd_re.match(t["name"])]

    # Check which are referenced by API keys
    query = """
    query {
      apiKeys(first: 100) { edges { node { id profiles } } }
    }
    """
    result = fetch_graphql(axonhub_url, token, query)
    api_keys = [e["node"] for e in result.get("data", {}).get("apiKeys", {}).get("edges", [])]

    referenced_ids = set()
    for key in api_keys:
        try:
            profiles = json.loads(key["profiles"]) if isinstance(key["profiles"], str) else key["profiles"]
            for profile in profiles.get("profiles", []):
                if profile.get("templateID"):
                    referenced_ids.add(profile["templateID"])
        except Exception:
            pass

    deleted = []
    for t in yymmdd_templates:
        if t["name"] != today and t["id"] not in referenced_ids:
            if dry_run:
                print(f"  [DRY RUN] Would delete template {t['name']}")
                deleted.append(t["name"])
            else:
                mutation = """
                mutation($id: ID!) { deleteApiKeyProfileTemplate(id: $id) { id } }
                """
                fetch_graphql(axonhub_url, token, mutation, {"id": t["id"]})
                deleted.append(t["name"])

    return deleted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Apply model mapping to AxonHub')
    parser.add_argument('--axonhub-url', type=str, required=True,
                        help='AxonHub base URL')
    parser.add_argument('--token', type=str, required=True,
                        help='JWT token')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without making changes')
    parser.add_argument('--mapping-file', type=Path, default=None,
                        help='Path to mapping CSV (default: auto-detect latest)')
    parser.add_argument('--channel-id', type=int, default=DEFAULT_CHANNEL_ID,
                        help=f'Channel ID for associations (default: {DEFAULT_CHANNEL_ID})')
    parser.add_argument('--skip-associations', action='store_true',
                        help='Only create template, skip association updates')
    parser.add_argument('--skip-cleanup', action='store_true',
                        help='Skip old template cleanup')
    args = parser.parse_args()

    # Find mapping file
    if args.mapping_file:
        mapping_path = args.mapping_file
    else:
        refs = Path(__file__).parent.parent / "references"
        candidates = sorted(refs.glob("mapping-*.csv"), reverse=True)
        if not candidates:
            print("Error: No mapping file found. Provide --mapping-file or place mapping-*.csv in references/", file=sys.stderr)
            sys.exit(1)
        mapping_path = candidates[0]
        print(f"Auto-detected mapping file: {mapping_path.name}")

    # Parse mapping
    mapping = parse_mapping_csv(mapping_path)
    if not mapping:
        print("Error: No mappings found in file", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print(f"Apply Mapping (from {mapping_path.name})")
    print(f"Channel ID: {args.channel_id}")
    print(f"Models to map: {len(mapping)}")
    print("=" * 60)
    print()

    # Show parsed mapping
    print("Parsed mapping:")
    for req, tgt in mapping.items():
        print(f"  {req:25s} -> {tgt}")
    print()

    # Step 1: Create/update template
    print("-" * 40)
    print("Step 1: Template")
    template_id, action = create_or_update_template(
        args.axonhub_url, args.token, mapping, args.dry_run,
    )
    print(f"  Template {action}: {template_id}")
    print()

    # Step 2: Update associations
    if not args.skip_associations:
        print("-" * 40)
        print("Step 2: Associations")
        model_ids = get_axonhub_model_ids(args.axonhub_url, args.token)
        print(f"  Found {len(model_ids)} models in AxonHub")
        success, errors = update_associations(
            args.axonhub_url, args.token, mapping, model_ids,
            args.channel_id, args.dry_run,
        )
        print(f"  Result: {success} ok, {errors} errors")
        print()

    # Step 3: Cleanup
    if not args.skip_cleanup:
        print("-" * 40)
        print("Step 3: Cleanup old templates")
        deleted = cleanup_old_templates(args.axonhub_url, args.token, args.dry_run)
        if deleted:
            print(f"  Deleted {len(deleted)} templates: {', '.join(deleted)}")
        else:
            print("  No unused templates")
        print()

    print("=" * 60)
    status = "DRY RUN" if args.dry_run else "DONE"
    print(f"{status}: {len(mapping)} models mapped via template + associations")
    print("=" * 60)


if __name__ == "__main__":
    main()
