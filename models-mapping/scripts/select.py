#!/usr/bin/env python3
"""Select, per model id, the channel whose snapshot reports the higher RP5H.

Input: one or more channel snapshots (provider envelopes such as
``data/opencode-go-models.json`` and ``data/goat-models.json``). The channel
name is the envelope's provider key. Output: ``data/model_select.json`` —
the union of model ids with the winning channel for each.

Selection rules
- RP5H is read from ``extra.rp5h`` (falling back to a top-level ``rp5h``).
- A model carried by a single channel belongs to that channel unchanged.
- When several channels carry the model, higher RP5H wins; a missing RP5H
  loses to any present value; an exact tie goes to the channel listed first
  on the command line; when no channel reports an RP5H the channel stays
  null so the ambiguity stays visible.

This is a channel-selection artifact; it does not touch the request→candidate
mapping in ``models.csv``.

Stdlib only. Usage:
  select.py --source data/opencode-go-models.json --source data/goat-models.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_OUTPUT = Path("data/model_select.json")
SCHEMA_VERSION = 1


def load_channel_snapshot(path: Path) -> tuple[str, dict[str, dict]]:
    """Return (channel_name, models) from a provider-envelope snapshot."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError(f"{path}: snapshot must be a non-empty object")
    providers = [key for key, value in payload.items() if isinstance(value, Mapping)]
    if len(providers) != 1 or not isinstance(payload[providers[0]].get("models"), Mapping):
        raise ValueError(
            f"{path}: expected one provider envelope with a models object, got keys {sorted(payload)}"
        )
    channel = providers[0]
    models = {
        str(model_id).strip(): dict(record)
        for model_id, record in payload[channel]["models"].items()
        if isinstance(record, Mapping) and str(model_id).strip()
    }
    if not models:
        raise ValueError(f"{path}: channel {channel!r} contains no models")
    return channel, models


def record_rp5h(record: Mapping[str, Any]) -> float | None:
    """Read RP5H from extra.rp5h, falling back to a top-level rp5h."""

    extra = record.get("extra")
    value = extra.get("rp5h") if isinstance(extra, Mapping) else None
    if value is None:
        value = record.get("rp5h")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def select_channels(
    snapshots: Sequence[tuple[str, Mapping[str, Mapping[str, Any]]]],
) -> dict[str, dict]:
    """Build the per-model selection over the union of channel model ids."""

    selection: dict[str, dict] = {}
    for model_id in sorted({mid for _, models in snapshots for mid in models}):
        candidates: dict[str, dict] = {}
        for order, (channel, models) in enumerate(snapshots):
            if model_id in models:
                candidates[channel] = {
                    "rp5h": record_rp5h(models[model_id]),
                    "order": order,
                }
        winner_channel, winner_rp5h = None, None
        if len(candidates) == 1:
            # Nothing to compare: the sole carrying channel keeps the model.
            only = next(iter(candidates.items()))
            winner_channel, winner_rp5h = only[0], only[1]["rp5h"]
        for channel, entry in candidates.items():
            rp5h = entry["rp5h"]
            if rp5h is None:
                continue
            if (
                winner_rp5h is None
                or rp5h > winner_rp5h
                or (rp5h == winner_rp5h and entry["order"] < candidates[winner_channel]["order"])
            ):
                winner_channel, winner_rp5h = channel, rp5h
        selection[model_id] = {
            "channel": winner_channel,
            "rp5h": winner_rp5h,
            "candidates": {
                channel: {"rp5h": entry["rp5h"]}
                for channel, entry in sorted(candidates.items())
            },
        }
    return selection


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def build_document(
    snapshots: Sequence[tuple[str, Mapping[str, Mapping[str, Any]]]],
    sources: Sequence[Path],
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [str(source) for source in sources],
        "models": select_channels(snapshots),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Select the higher-RP5H channel per model across channel snapshots"
    )
    parser.add_argument(
        "--source",
        type=Path,
        action="append",
        required=True,
        help="Channel snapshot (provider envelope); repeat for each channel",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args(argv)

    try:
        snapshots: list[tuple[str, dict[str, dict]]] = []
        seen_channels: set[str] = set()
        for source in args.source:
            channel, models = load_channel_snapshot(source)
            if channel in seen_channels:
                raise ValueError(f"{source}: channel {channel!r} given more than once")
            seen_channels.add(channel)
            snapshots.append((channel, models))
        document = build_document(snapshots, args.source)
        write_json_atomic(args.output, document)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"select: {exc}", file=sys.stderr)
        return 1

    contested = sum(1 for entry in document["models"].values() if len(entry["candidates"]) > 1)
    print(
        f"select: {len(document['models'])} models "
        f"({contested} in multiple channels) -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
