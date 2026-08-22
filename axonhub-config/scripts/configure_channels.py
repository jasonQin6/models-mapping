#!/usr/bin/env python3
"""
Configure AxonHub channel tags and ordering weights.

Tags format: quotaXXXX (where XXXX is the per-5h request quota)
Weight range: 0-10 (10 = highest priority)

Usage:
    python3 scripts/configure_channels.py --axonhub-url <URL> --token <JWT>
    python3 scripts/configure_channels.py --axonhub-url <URL> --token <JWT> --dry-run
"""

import argparse
import sys

from common import fetch_graphql, CHANNELS


def fetch_channels(axonhub_url, token):
    """Fetch all channels with current config."""
    query = """
    query {
      channels(first: 20) {
        edges {
          node {
            id
            name
            type
            orderingWeight
            tags
            supportedModels
          }
        }
      }
    }
    """
    response = fetch_graphql(axonhub_url, token, query)
    edges = response.get("data", {}).get("channels", {}).get("edges", [])

    channels = {}
    for e in edges:
        node = e["node"]
        ch_id = int(node["id"].split("/")[-1])
        channels[ch_id] = {
            "name": node["name"],
            "type": node["type"],
            "weight": node.get("orderingWeight", 0),
            "tags": node.get("tags") or [],
            "models": len(node.get("supportedModels", [])),
        }
    return channels


def update_channel(axonhub_url, token, channel_id, weight, tags, dry_run=False):
    """Update channel weight and tags."""
    if dry_run:
        return True

    mutation = """
    mutation($id: ID!, $input: UpdateChannelInput!) {
      updateChannel(id: $id, input: $input) {
        id
        name
        orderingWeight
        tags
      }
    }
    """

    response = fetch_graphql(axonhub_url, token, mutation, {
        "id": f"gid://axonhub/Channel/{channel_id}",
        "input": {
            "orderingWeight": weight,
            "tags": tags
        }
    })

    if "errors" in response:
        print(f"  ERROR: {response['errors']}")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description='Configure AxonHub channel tags and weights')
    parser.add_argument('--axonhub-url', type=str, default='https://axon.jasonqin.site',
                        help='AxonHub URL')
    parser.add_argument('--token', type=str, required=True,
                        help='JWT token')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be configured without making changes')
    args = parser.parse_args()

    print("=" * 80)
    print("AxonHub Channel Configuration")
    print("=" * 80)
    print()
    print("Tag format: quotaXXXX (XXXX = per-5h request quota)")
    print("Weight range: 0-10 (10 = highest priority)")
    print()

    # Fetch current state
    print("Fetching current channel configuration...")
    current_channels = fetch_channels(args.axonhub_url, args.token)
    print(f"Found {len(current_channels)} channels")
    print()

    # Display current state
    print("Current configuration:")
    print(f"{'ID':>4s} | {'Name':20s} | {'Weight':>6s} | {'Tags':20s} | {'Models':>5s}")
    print("-" * 70)
    for ch_id in sorted(current_channels.keys()):
        ch = current_channels[ch_id]
        tags = ','.join(ch['tags'])
        print(f"{ch_id:>4} | {ch['name']:20s} | {ch['weight']:>6} | {tags:20s} | {ch['models']:>5}")
    print()

    # Apply changes
    updated_count = 0
    skipped_count = 0

    for ch_id, config in sorted(CHANNELS.items()):
        if ch_id not in current_channels:
            print(f"SKIP: ch{ch_id} ({config['name']}) not found on server")
            skipped_count += 1
            continue

        current = current_channels[ch_id]
        new_weight = config['weight']
        new_tags = config['tags']

        weight_changed = current['weight'] != new_weight
        tags_changed = current['tags'] != new_tags

        if not weight_changed and not tags_changed:
            print(f"  OK ch{ch_id} {current['name']:20s} (no changes)")
            skipped_count += 1
            continue

        changes = []
        if weight_changed:
            changes.append(f"weight {current['weight']} -> {new_weight}")
        if tags_changed:
            changes.append(f"tags {current['tags']} -> {new_tags}")

        print(f"  Updating ch{ch_id} {current['name']}: {', '.join(changes)}")

        if update_channel(args.axonhub_url, args.token, ch_id, new_weight, new_tags, args.dry_run):
            if args.dry_run:
                print(f"    [DRY RUN] Would update")
            else:
                print(f"    OK Updated")
            updated_count += 1
        else:
            print(f"    FAILED")

    print()
    print("=" * 80)
    print(f"Summary: {updated_count} updated, {skipped_count} skipped")
    print("=" * 80)


if __name__ == "__main__":
    main()
