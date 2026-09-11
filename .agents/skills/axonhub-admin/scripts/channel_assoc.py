#!/usr/bin/env python3
"""Step 3 — non-Claude channel associations: route models to serving channels.

Renders the registry's serving facts into the per-model ``channelPriority``
chain: every serving channel ordered by descending rp5h — p0 is the
dedupe-winning primary channel, later entries are the fallback order;
single-channel models degrade to a p0 pin.  Free family merging and the
optional reinforcements (strict fallback demotion, time-gated routing) are
per-model conventions applied by the interactive session per the skill
docs — this script only emits the deterministic facts.

Pure offline: no credentials, no network, no AxonHub writes.

Usage:
    python3 .agents/skills/axonhub-admin/scripts/channel_assoc.py [--id <modelID>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from registry import build_registry, is_free_model  # noqa: E402
from snapshot import (  # noqa: E402
    PlanningError,
    load_arena,
    load_extra_aliases,
    load_extra_blocklist,
    load_extra_sections,
)

# The two collected channels whose shared models drive channel-priority
# associations (ADR 0016); the static sections (ant, sensenova) are not part
# of the intersection.
INTERSECTION_CHANNELS = ("commandcode-goat", "opencode-go")


def build_associations(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Render per-model channelPriority chains and default association drafts."""

    plans: list[dict[str, Any]] = []
    for canonical in sorted(result["registry"]):
        entry = result["registry"][canonical]
        record = entry["record"]
        # Association plan (ADR 0016): channel_model rules chained by
        # descending rp5h — p0 is the primary channel, later entries are the
        # fallback order. Single-channel models degrade to a p0 pin.
        ranked_channels = sorted(
            entry["serving"].items(),
            key=lambda item: (-(item[1] if item[1] is not None else -1.0), item[0]),
        )
        channel_priority = [
            {"channel": channel, "rp5h": rp5h, "priority": priority}
            for priority, (channel, rp5h) in enumerate(ranked_channels)
        ]
        plan = {
            "modelID": canonical,
            "free": is_free_model(canonical, record),
            "channelPriority": channel_priority,
        }
        if entry["channel_aliases"]:
            plan["channelAliases"] = dict(sorted(entry["channel_aliases"].items()))
        plans.append(plan)
    return plans


def intersection_count(result: Mapping[str, Any]) -> int:
    return sum(
        1
        for entry in result["registry"].values()
        if all(channel in entry["serving"] for channel in INTERSECTION_CHANNELS)
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute per-model channel-priority association chains from the snapshots"
    )
    parser.add_argument("--extra", type=Path, default=Path("data/models_extra.json"))
    parser.add_argument("--arena", type=Path, default=Path("data/arena.json"))
    parser.add_argument("--id", default=None, help="restrict output to one modelID")
    args = parser.parse_args(argv)

    try:
        sections = load_extra_sections(args.extra)
        aliases = load_extra_aliases(args.extra)
        blocklist_raw = load_extra_blocklist(args.extra)
        arena_models = load_arena(args.arena)
        result = build_registry(
            sections=sections,
            aliases=aliases,
            blocklist_raw=blocklist_raw,
            arena_models=arena_models,
        )
    except PlanningError as exc:
        print(f"channel-assoc: {exc}", file=sys.stderr)
        return 1

    plans = build_associations(result)
    if args.id:
        plans = [plan for plan in plans if plan["modelID"] == args.id]
        if not plans:
            print(f"channel-assoc: {args.id!r} is not in the registry", file=sys.stderr)
            return 1

    print(
        f"channel-assoc: {len(plans)} model plan(s), "
        f"intersection={intersection_count(result)}",
        file=sys.stderr,
    )
    for warning in result["warnings"]:
        fields = " ".join(f"{key}={warning[key]!r}" for key in warning if key != "type")
        print(f"WARNING [{warning['type']}] {fields}", file=sys.stderr)

    print(json.dumps(plans, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
