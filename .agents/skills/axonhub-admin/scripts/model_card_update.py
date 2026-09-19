#!/usr/bin/env python3
"""Step 2 — model-card-update: compute the AxonHub model-card target state.

Builds the offline target state from the registry: one entry per model with
its owning channel, card reference into ``data/all_models.json``, derived
meta (developer/icon/group), merged cost end values, and structured remark.
The interactive session diffs this target against the live AxonHub state
(read-before-write) and applies the increment through the skill's execution
loop — this script never writes to AxonHub.

``--id <modelID>`` renders the full write-time payload for one model: the
referenced public card expanded into AxonHub's modelCard shape with the
channel-derived cost on top (a null reference renders the default card).

Pure offline: no credentials, no network, no AxonHub writes.

Usage:
    python3 .agents/skills/axonhub-admin/scripts/model_card_update.py [--id <modelID>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from registry import build_registry, is_free_model  # noqa: E402
from snapshot import (  # noqa: E402
    PlanningError,
    as_dict,
    load_arena,
    load_cards,
    load_extra_aliases,
    BLOCKLIST_PATH,
    load_blocklist,
    load_extra_sections,
)

REMARK_FIELDS = ("rp5h", "usage_quota")

# Version-succession triage: a plain ``<family>-<major>.<minor>`` id whose
# next-minor successor scores higher (beyond Arena ELO noise) at a comparable
# quota tier (rp5h ratio below the limit, either direction) looks fully
# displaced.  Report-only: the suggestion lands in stderr for the maintainer
# to rule on — never auto-materialized into the blocklist.
VERSION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-v?\d+\.\d+$")
VERSION_SCORE_MARGIN = 10.0
VERSION_RP5H_RATIO_LIMIT = 2.0


def retire_suggestions(
    result: Mapping[str, Any], warnings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Emit per-pair succession suggestions into the warning stream.

    Pairing is adjacency-only within one ``(family, major)`` group of plain
    versioned ids — variant suffixes and tier words (``-flash``/``-max``/…)
    never pair.  Each suggestion lists the channels still serving the
    predecessor (native spelling where one exists) so the maintainer can see
    what a retirement would rewire.
    """

    registry = result["registry"]
    standings = {candidate["model_id"]: candidate for candidate in result["candidates"]}

    groups: dict[tuple[str, int], list[tuple[int, str]]] = {}
    for model_id in registry:
        lowered = model_id.lower()
        if not VERSION_ID_PATTERN.match(lowered):
            continue
        head, _, minor = lowered.rpartition(".")
        family, _, major = head.rpartition("-")
        groups.setdefault((family, int(major.lstrip("v"))), []).append((int(minor), model_id))

    suggestions: list[dict[str, Any]] = []
    for _family_major, members in sorted(groups.items()):
        members.sort()
        for (minor, predecessor), (_next_minor, successor) in zip(members, members[1:]):
            before, after = standings.get(predecessor), standings.get(successor)
            if not before or not after:
                continue
            scores = (before.get("arena_score"), after.get("arena_score"))
            quotas = (before.get("rp5h"), after.get("rp5h"))
            if None in scores or None in quotas:
                continue
            if after["arena_score"] <= before["arena_score"] + VERSION_SCORE_MARGIN:
                continue
            if min(quotas) <= 0 or max(quotas) / min(quotas) >= VERSION_RP5H_RATIO_LIMIT:
                continue
            entry = registry[predecessor]
            serving = {
                channel: (entry.get("channel_aliases") or {}).get(channel) or predecessor
                for channel in (entry.get("serving") or {})
            }
            suggestion = {
                "type": "retire_suggested",
                "model": predecessor,
                "replaced_by": successor,
                "arena": {"before": before["arena_score"], "after": after["arena_score"]},
                "rp5h": {"before": before["rp5h"], "after": after["rp5h"]},
                "serving": dict(sorted(serving.items())),
            }
            suggestions.append(suggestion)
            warnings.append(suggestion)
    return suggestions


def _cost_from_model(model: Mapping[str, Any]) -> dict[str, Any]:
    raw_cost = as_dict(model.get("cost"))
    nested_cache = as_dict(raw_cost.get("cache"))
    return {
        "input": raw_cost.get("input", 0),
        "output": raw_cost.get("output", 0),
        "cacheRead": raw_cost.get("cache_read", raw_cost.get("cacheRead", nested_cache.get("read", 0))),
        "cacheWrite": raw_cost.get("cache_write", raw_cost.get("cacheWrite", nested_cache.get("write", 0))),
    }


def _merge_channel_cost(
    card: Mapping[str, Any], record: Mapping[str, Any], is_free: bool
) -> dict[str, Any]:
    """Start from the card cost and overwrite with non-null channel values.

    A free record is the channel declaring every price zero: cost fields it
    leaves silent start at 0 so the card's list price cannot leak into a
    free model's card.
    """

    if is_free:
        cost = {key: 0 for key in ("input", "output", "cacheRead", "cacheWrite")}
    else:
        cost = _cost_from_model(card)
    channel_cost = as_dict(record.get("cost"))
    aliases = {"input": "input", "output": "output", "cache_read": "cacheRead", "cacheRead": "cacheRead",
               "cache_write": "cacheWrite", "cacheWrite": "cacheWrite"}
    for key, value in channel_cost.items():
        target = aliases.get(key)
        if target is not None and value is not None:
            cost[target] = value
    return cost


def model_card(model: Mapping[str, Any]) -> dict[str, Any]:
    """Map source capabilities to AxonHub's modelCard shape."""

    modalities = as_dict(model.get("modalities"))
    limit = as_dict(model.get("limit"))
    return {
        "reasoning": {"supported": bool(model.get("reasoning", False)), "default": bool(model.get("reasoning", False))},
        "toolCall": bool(model.get("tool_call", model.get("toolCall", model.get("toolcall", False)))),
        "temperature": bool(model.get("temperature", True)),
        "modalities": {"input": modalities.get("input", ["text"]), "output": modalities.get("output", ["text"])},
        "vision": "image" in modalities.get("input", []),
        "cost": _cost_from_model(model),
        "limit": {"context": limit.get("context", 0), "output": limit.get("output", 0)},
        "knowledge": model.get("knowledge") or "",
        "releaseDate": model.get("release_date") or model.get("releaseDate") or "",
        "lastUpdated": model.get("last_updated") or model.get("lastUpdated") or "",
    }


def remark_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _model_meta(model: Mapping[str, Any], provider: str) -> tuple[str, str, str]:
    family = str(model.get("family") or provider)
    lowered = family.lower()
    if lowered.startswith("deepseek"):
        return "deepseek", "DeepSeek", family
    if lowered.startswith("glm"):
        return "zai", "ChatGLM", "glm"
    if lowered.startswith("gpt"):
        return "openai", "OpenAI", family
    if lowered.startswith("grok"):
        return "xai", "XAI", "grok"
    if lowered.startswith("kimi"):
        return "moonshot", "Moonshot", family
    if lowered.startswith("longcat"):
        return "longcat", "LongCat", "longcat"
    if lowered.startswith("mimo"):
        return "xiaomi", "XiaomiMiMo", "mimo"
    if lowered.startswith("minimax"):
        return "minimax", "MiniMax", family
    if lowered.startswith("muse"):
        return "meta", "Meta", "muse"
    if lowered.startswith("qwen"):
        return "alibaba", "Qwen", family
    if lowered.startswith("hy"):
        return "hy", "Default", "hy"
    return provider, "Default", family


def build_target_state(
    result: Mapping[str, Any],
    cards: Mapping[str, Mapping[str, Any]],
    card_refs: Mapping[str, str],
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Render the registry into per-model card target-state entries.

    Card fields come only from ``all_models.json``: ``cardRef`` keeps the
    original ``vendor/model`` key, a missing card yields ``cardRef: null``
    plus a ``card_missing`` warning (never invented).  Cost starts from the
    card and lets the channel's declared fields override field by field.
    """

    models: list[dict[str, Any]] = []
    for canonical in sorted(result["registry"]):
        entry = result["registry"][canonical]
        record = entry["record"]
        axon_channel = entry["channel"]
        card = cards.get(canonical) or cards.get(entry["native_id"])
        card_ref = card_refs.get(canonical) or card_refs.get(entry["native_id"])
        if card is None:
            warnings.append({"type": "card_missing", "model": canonical, "provider": entry["channel"]})
        merged = dict(card or {})
        developer, icon, group = _model_meta(merged, axon_channel)
        remark = {"manual": ""}
        for field in REMARK_FIELDS:
            remark[field] = record.get(field)
        missing = [field for field in REMARK_FIELDS if remark[field] is None]
        if missing:
            warnings.append({"type": "missing_remark_fields", "model": canonical, "fields": missing})
        model_entry = {
            "modelID": canonical,
            "channel": axon_channel,
            "cardRef": card_ref if card is not None else None,
            "input": {
                "name": str(merged.get("name") or record.get("name") or canonical),
                "developer": developer,
                "type": "chat",
                "icon": icon,
                "group": group,
                "cost": _merge_channel_cost(card or {}, record, is_free_model(record)),
                "remark": remark_json(remark),
            },
        }
        if entry["channel_aliases"]:
            model_entry["channelAliases"] = dict(sorted(entry["channel_aliases"].items()))
        models.append(model_entry)
    return models


def assemble(entry: Mapping[str, Any], catalog: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Assemble the full AxonHub model input from a target-state entry.

    ``modelCard``'s descriptive fields come from the referenced
    ``data/all_models.json`` card through ``model_card()`` (the single
    mapping source); ``cost`` and ``remark`` are the channel-derived end
    values.  A ``cardRef`` of null (``card_missing`` models) renders the
    default card.
    """

    plan_input = dict(entry.get("input") or {})
    card_ref = entry.get("cardRef")
    card = catalog.get(card_ref) if card_ref else None
    model_card_payload = model_card(card or {})
    model_card_payload["cost"] = dict(plan_input.get("cost") or {})
    return {
        "modelID": entry["modelID"],
        "name": plan_input.get("name"),
        "developer": plan_input.get("developer"),
        "type": plan_input.get("type", "chat"),
        "icon": plan_input.get("icon"),
        "group": plan_input.get("group"),
        "modelCard": model_card_payload,
        "remark": plan_input.get("remark", ""),
    }


def _render_stderr(warnings: Sequence[Mapping[str, Any]], models: Sequence[Mapping[str, Any]]) -> None:
    print(f"model-card-update: {len(models)} model(s) in target state", file=sys.stderr)
    for warning in warnings:
        fields = " ".join(f"{key}={warning[key]!r}" for key in warning if key != "type")
        print(f"WARNING [{warning['type']}] {fields}", file=sys.stderr)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute the AxonHub model-card target state from the snapshots"
    )
    parser.add_argument("--extra", type=Path, default=Path("data/models_extra.json"))
    parser.add_argument("--blocklist", type=Path, default=BLOCKLIST_PATH)
    parser.add_argument("--cards", type=Path, default=Path("data/all_models.json"))
    parser.add_argument("--arena", type=Path, default=Path("data/arena.json"))
    parser.add_argument("--id", default=None, help="render the full write-time payload for one modelID")
    args = parser.parse_args(argv)

    try:
        sections = load_extra_sections(args.extra)
        aliases = load_extra_aliases(args.extra)
        blocklist_raw = load_blocklist(args.blocklist)
        arena_models = load_arena(args.arena)
        cards, refs = load_cards(args.cards)
        result = build_registry(
            sections=sections,
            aliases=aliases,
            blocklist_raw=blocklist_raw,
            arena_models=arena_models,
        )
    except PlanningError as exc:
        print(f"model-card-update: {exc}", file=sys.stderr)
        return 1

    warnings = list(result["warnings"])
    models = build_target_state(result, cards, refs, warnings)
    retire_suggestions(result, warnings)
    _render_stderr(warnings, models)

    if args.id:
        entry = next((item for item in models if item["modelID"] == args.id), None)
        if entry is None:
            print(f"model-card-update: {args.id!r} is not in the target state", file=sys.stderr)
            return 1
        catalog = {refs[bare]: cards[bare] for bare in refs}
        print(json.dumps(assemble(entry, catalog), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(json.dumps(models, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
