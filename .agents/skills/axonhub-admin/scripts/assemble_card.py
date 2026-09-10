#!/usr/bin/env python3
"""Render a model-plan entry into the full AxonHub model input.

The model plan is incremental (ADR 0017): it references the public card
via ``cardRef`` instead of copying it. This tool performs the write-time
assembly for axonhub-admin — one model, one JSON payload:

- ``modelCard``'s descriptive fields come from the referenced
  ``data/all_models.json`` card through ``model_card()`` (the single
  mapping source, shared with planning);
- ``cost`` and ``remark`` are the plan's channel-derived end values;
- ``modelID``, ``name``, ``developer``, ``icon``, ``group``, ``type``
  are the plan's derived meta.

A ``cardRef`` of null (``card_missing`` models) renders the default card,
matching what the planner would have produced.

Usage:
    python3 .agents/skills/axonhub-admin/scripts/assemble_card.py --id glm-5.3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from models_mapping import load_json, model_card  # noqa: E402


def assemble(entry: Mapping[str, Any], cards: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Assemble the full AxonHub model input from a plan entry."""

    plan_input = dict(entry.get("input") or {})
    card_ref = entry.get("cardRef")
    card = cards.get(card_ref) if card_ref else None
    model_card_payload = model_card(card or {})
    model_card_payload["cost"] = dict(plan_input.get("cost") or {})
    assembled = {
        "modelID": entry["modelID"],
        "name": plan_input.get("name"),
        "developer": plan_input.get("developer"),
        "type": plan_input.get("type", "chat"),
        "icon": plan_input.get("icon"),
        "group": plan_input.get("group"),
        "modelCard": model_card_payload,
        "remark": plan_input.get("remark", ""),
    }
    return assembled


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a model-plan entry into the full AxonHub model input"
    )
    parser.add_argument("--model-plan", type=Path, default=Path("data/model-plan.json"))
    parser.add_argument("--cards", type=Path, default=Path("data/all_models.json"))
    parser.add_argument("--id", required=True, help="modelID of the plan entry")
    args = parser.parse_args(argv)

    plan = load_json(args.model_plan)
    if not isinstance(plan, Mapping) or not isinstance(plan.get("models"), list):
        print(f"assemble-card: {args.model_plan} is not a model plan", file=sys.stderr)
        return 1
    entry = next(
        (item for item in plan["models"] if isinstance(item, Mapping) and item.get("modelID") == args.id),
        None,
    )
    if entry is None:
        print(f"assemble-card: {args.id!r} is not in {args.model_plan}", file=sys.stderr)
        return 1

    cards_payload = load_json(args.cards)
    cards: dict[str, dict[str, Any]] = {}
    if isinstance(cards_payload, Mapping):
        for key, value in cards_payload.items():
            if isinstance(value, Mapping):
                cards[str(key)] = dict(value)

    print(json.dumps(assemble(entry, cards), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
