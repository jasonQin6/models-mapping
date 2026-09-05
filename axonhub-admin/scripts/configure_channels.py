#!/usr/bin/env python3
"""
Configure AxonHub channel tags and ordering weights.

Channels are matched by exact channel name (CHANNELS keys in common.py);
the server-assigned numeric ID is read live and never used as the join key.
Channels on the server that are absent from CHANNELS are reported and left
untouched.

Tags format: quotaXXXX (where XXXX is the per-5h request quota)
Weight range: 0-10 (10 = highest priority)

Usage:
    AXONHUB_JWT=<JWT> python3 scripts/configure_channels.py --dry-run
    AXONHUB_JWT=<JWT> python3 scripts/configure_channels.py
"""

import argparse
import os

from common import fetch_connection, fetch_graphql, CHANNELS


def fetch_channels(axonhub_url: str, token: str) -> list[dict]:
    """Fetch all channels with current config, keeping the server-assigned ID."""
    nodes = fetch_connection(
        axonhub_url,
        token,
        "channels",
        "id name type orderingWeight tags supportedModels",
    )

    channels = []
    for node in nodes:
        channels.append({
            "id": int(node["id"].split("/")[-1]),
            "name": node["name"],
            "type": node["type"],
            "weight": node.get("orderingWeight", 0),
            "tags": node.get("tags") or [],
            "models": len(node.get("supportedModels", [])),
        })
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

    try:
        fetch_graphql(axonhub_url, token, mutation, {
            "id": f"gid://axonhub/Channel/{channel_id}",
            "input": {
                "orderingWeight": weight,
                "tags": tags
            }
        })
    except RuntimeError as exc:
        print(f"  ERROR: {exc}")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description='Configure AxonHub channel tags and weights')
    parser.add_argument('--axonhub-url', type=str, default='https://axon.jasonqin.site',
                        help='AxonHub URL')
    parser.add_argument('--token', type=str, default=os.environ.get('AXONHUB_JWT'),
                        help='JWT token')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be configured without making changes')
    args = parser.parse_args()
    if not args.token:
        parser.error('provide --token or set AXONHUB_JWT')

    print("=" * 80)
    print("AxonHub Channel Configuration")
    print("=" * 80)
    print()
    print("Tag format: quotaXXXX (XXXX = per-5h request quota)")
    print("Weight range: 0-10 (10 = highest priority)")
    print("Channels are matched by name; server-assigned IDs are informational.")
    print()

    # Fetch current state
    print("Fetching current channel configuration...")
    current_channels = fetch_channels(args.axonhub_url, args.token)
    print(f"Found {len(current_channels)} channels")
    print()

    live_by_name: dict[str, list[dict]] = {}
    for ch in current_channels:
        live_by_name.setdefault(ch["name"], []).append(ch)

    # Display current state
    print("Current configuration:")
    print(f"{'ID':>4s} | {'Name':20s} | {'Weight':>6s} | {'Tags':20s} | {'Models':>5s}")
    print("-" * 70)
    for ch in sorted(current_channels, key=lambda c: c["id"]):
        tags = ','.join(ch['tags'])
        print(f"{ch['id']:>4} | {ch['name']:20s} | {ch['weight']:>6} | {tags:20s} | {ch['models']:>5}")
    print()

    # Apply changes
    updated_count = 0
    skipped_count = 0

    for name, config in CHANNELS.items():
        matches = live_by_name.get(name, [])
        if not matches:
            print(f"SKIP: {name} not found on server")
            skipped_count += 1
            continue
        if len(matches) > 1:
            ids = ", ".join(str(m["id"]) for m in matches)
            print(f"SKIP: {name} matched {len(matches)} channels (ids {ids}) — refusing to guess")
            skipped_count += 1
            continue

        current = matches[0]
        new_weight = config['weight']
        new_tags = config['tags']

        weight_changed = current['weight'] != new_weight
        tags_changed = current['tags'] != new_tags

        if not weight_changed and not tags_changed:
            print(f"  OK {name:20s} ch{current['id']} (no changes)")
            skipped_count += 1
            continue

        changes = []
        if weight_changed:
            changes.append(f"weight {current['weight']} -> {new_weight}")
        if tags_changed:
            changes.append(f"tags {current['tags']} -> {new_tags}")

        print(f"  Updating {name} (ch{current['id']}): {', '.join(changes)}")

        if update_channel(args.axonhub_url, args.token, current['id'], new_weight, new_tags, args.dry_run):
            if args.dry_run:
                print(f"    [DRY RUN] Would update")
            else:
                print(f"    OK Updated")
            updated_count += 1
        else:
            print(f"    FAILED")

    untouched = [ch["name"] for ch in current_channels if ch["name"] not in CHANNELS]
    if untouched:
        print()
        print(f"Not in CHANNELS, untouched: {', '.join(sorted(untouched))}")

    print()
    print("=" * 80)
    print(f"Summary: {updated_count} updated, {skipped_count} skipped")
    print("=" * 80)


if __name__ == "__main__":
    main()
