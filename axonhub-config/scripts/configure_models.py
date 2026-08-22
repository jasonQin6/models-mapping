#!/usr/bin/env python3
"""
Configure model associations based on channel quota and billing type.

Channel priority rules:
1. Count-based billing (计次) > Token-based billing (按 token)
2. Within same billing: higher quota > lower quota
3. opencode-go deepseek/qwen series: reduced priority (peak/off-peak pricing)
4. Ali-Token: night-only models (22:00-08:00) get priority boost

Priority scoring:
- Count-based: base = 0 - quota (negative, lower = higher priority)
- Token-based: base = 100000 - quota (positive, always after count-based)
- Adjustments: +10000 for opencode-go deepseek/qwen, -5000 for Ali-Token night models
"""

import argparse

from common import fetch_graphql, CHANNELS, ALI_TOKEN_NIGHT_MODELS


def calculate_channel_priority(channel_id, model_id):
    """
    Calculate priority for a channel-model pair.
    Lower value = higher priority.

    Count-based: 0 - quota (negative range: -6000 to -1500)
    Token-based: 100000 - quota (positive range: 54700 to 100000)

    This ensures count-based channels always have higher priority than token-based.

    Returns (priority_score, channel_name)
    """
    ch = CHANNELS.get(channel_id)
    if not ch:
        return (999999, "unknown")

    quota = ch["quota"]
    billing = ch["billing"]

    if billing == "count":
        # Count-based: 0 - quota (negative, lower = higher priority)
        # Ali-Coding: -6000, Sensenova: -1500
        base = 0 - quota
    else:
        # Token-based: 100000 - quota (positive, always after count-based)
        # opencode-go: 54700, GLM: 99920, Ali-Token: 99900, deepseek: 100000
        base = 100000 - quota

    # Model-specific adjustments
    if channel_id == 6:  # opencode-go
        if model_id.startswith("deepseek") or model_id.startswith("qwen"):
            # +10000: push below other token-based models in same channel
            # because deepseek/qwen have peak/off-peak pricing that makes them less attractive
            base += 10000

    if channel_id == 5:  # Ali-Token
        if model_id in ALI_TOKEN_NIGHT_MODELS:
            # -5000: boost night-only models above daytime Ali-Token models
            # so they get picked first during night hours (22:00-08:00)
            base -= 5000

    return (base, ch["name"])


def get_model_channels(model_id, channel_models_map):
    """Get all channels that support a model, sorted by priority."""
    channels = []
    for ch_id, models in channel_models_map.items():
        if model_id in models:
            priority, ch_name = calculate_channel_priority(ch_id, model_id)
            channels.append((priority, ch_id, ch_name))

    channels.sort(key=lambda x: x[0])
    return channels


def main():
    parser = argparse.ArgumentParser(description='Configure model associations based on channel quota')
    parser.add_argument('--axonhub-url', type=str, default='https://axon.jasonqin.site',
                        help='AxonHub URL')
    parser.add_argument('--token', type=str, required=True,
                        help='JWT token')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be configured without making changes')
    args = parser.parse_args()

    print("=" * 70)
    print("Model Association Configuration")
    print("=" * 70)
    print()
    print("Priority scoring:")
    print("  Count-based:  0 - quota (negative, always first)")
    print("  Token-based:  100000 - quota (positive, after count-based)")
    print("  Adjustments:  +10000 for opencode-go deepseek/qwen")
    print("                -5000 for Ali-Token night models")
    print()

    # Fetch all channels with supported models
    print("Fetching channels...")
    channels_query = """
    query {
      channels(first: 20) {
        edges {
          node {
            id
            name
            type
            supportedModels
          }
        }
      }
    }
    """

    response = fetch_graphql(args.axonhub_url, args.token, channels_query)
    edges = response.get("data", {}).get("channels", {}).get("edges", [])

    channel_models_map = {}
    for e in edges:
        node = e["node"]
        ch_id = int(node["id"].split("/")[-1])
        channel_models_map[ch_id] = set(node.get("supportedModels", []))

    print(f"Found {len(channel_models_map)} channels")
    for ch_id in sorted(channel_models_map.keys()):
        models = channel_models_map[ch_id]
        ch = CHANNELS.get(ch_id, {})
        print(f"  ch{ch_id}: {ch.get('name', 'unknown'):20s} | {len(models):3d} models | quota={ch.get('quota', '?'):>6} | {ch.get('billing', '?')}")
    print()

    # Fetch all models
    print("Fetching models...")
    models_query = """
    query {
      models(first: 50) {
        edges {
          node {
            id
            modelID
            name
          }
        }
      }
    }
    """

    response = fetch_graphql(args.axonhub_url, args.token, models_query)
    edges = response.get("data", {}).get("models", {}).get("edges", [])

    print(f"Found {len(edges)} models")
    print()

    mutation = """
    mutation UpdateModel($id: ID!, $input: UpdateModelInput!) {
      updateModel(id: $id, input: $input) {
        id
        modelID
        settings {
          associations {
            type
            priority
            channelModel {
              channelId
              modelId
            }
          }
        }
      }
    }
    """

    updated_count = 0
    skipped_count = 0

    for e in edges:
        node = e["node"]
        model_id = node["modelID"]
        internal_id = node["id"]

        # Skip Claude and GPT (handled by apply_mapping.py)
        if model_id.startswith("claude") or model_id.startswith("gpt"):
            continue

        channels = get_model_channels(model_id, channel_models_map)

        if not channels:
            print(f"  SKIP: {model_id:30s} (no channels)")
            skipped_count += 1
            continue

        # Build associations
        associations = []
        for i, (priority, ch_id, ch_name) in enumerate(channels):
            associations.append({
                "type": "channel_model",
                "priority": i,
                "disabled": False,
                "channelModel": {
                    "channelId": ch_id,
                    "modelId": model_id
                }
            })

        if args.dry_run:
            ch_strs = []
            for i, (priority, ch_id, ch_name) in enumerate(channels):
                marker = "*" if i == 0 else " "
                ch_strs.append(f"{marker}{ch_name}(ch{ch_id})")
            print(f"  {model_id:30s} {' | '.join(ch_strs)}")
            updated_count += 1
        else:
            response = fetch_graphql(args.axonhub_url, args.token, mutation, {
                "id": internal_id,
                "input": {
                    "settings": {
                        "associations": associations
                    }
                }
            })

            if "errors" in response:
                print(f"  ERROR: {model_id} - {response['errors']}")
                skipped_count += 1
            else:
                ch_strs = []
                for i, (priority, ch_id, ch_name) in enumerate(channels):
                    marker = "*" if i == 0 else " "
                    ch_strs.append(f"{marker}{ch_name}(ch{ch_id})")
                print(f"  OK {model_id:30s} {' | '.join(ch_strs)}")
                updated_count += 1

    print()
    print("=" * 70)
    print(f"Summary: {updated_count} updated, {skipped_count} skipped")
    print("=" * 70)


if __name__ == "__main__":
    main()
