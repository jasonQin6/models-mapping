#!/usr/bin/env python3
"""Snapshot loaders shared by every axonhub-admin step script.

Each step (channel-sync, model-card-update, channel associations, Claude
mapping) computes offline from the in-repo snapshots collected by
watch-pipeline: ``data/models_extra.json`` (channel-declared facts plus the
hand-maintained ``aliases`` top-level key), ``data/all_models.json`` (public
cards), ``data/arena.json`` (quality signals), and the step-side exclusion
store ``data/blocklist.json``.  This module owns turning those files into
the lookup shapes the steps consume; it never writes, never reaches the
network, and holds no credentials.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Optional

from name_matching import normalize_arena_name  # noqa: E402

EXTRA_SCHEMA_VERSION = 1
BLOCKLIST_PATH = Path("data/blocklist.json")


class PlanningError(RuntimeError):
    """A user-actionable planning failure."""


def as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or isinstance(value, bool) or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed):
        return default
    return parsed


def missing(value: Any) -> bool:
    return value is None or value == "" or value == "-"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanningError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlanningError(f"{path} is not valid JSON: {exc}") from exc


def load_extra_sections(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Load models_extra.json into ``{channel: {model_id: record}}``."""

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    version = payload.get("schema_version")
    if version is not None and version != EXTRA_SCHEMA_VERSION:
        raise PlanningError(f"{path} has unsupported schema_version {version!r}")
    channels = payload.get("channels")
    if not isinstance(channels, Mapping) or not channels:
        raise PlanningError(f"{path} carries no channel sections")
    sections: dict[str, dict[str, dict[str, Any]]] = {}
    for channel, models in channels.items():
        if not isinstance(models, Mapping):
            raise PlanningError(f"{path} channel {channel!r} is not an object")
        sections[str(channel)] = {
            str(model_id): dict(record)
            for model_id, record in models.items()
            if isinstance(record, Mapping)
        }
    return sections


def load_extra_aliases(path: Path) -> dict[str, str]:
    """Load the hand-maintained ``aliases`` map (channel-native -> canonical)."""

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        return {}
    aliases = payload.get("aliases")
    if aliases is None:
        return {}
    if not isinstance(aliases, Mapping):
        raise PlanningError(f"{path} aliases must be an object")
    return {str(alias): str(canonical) for alias, canonical in aliases.items()}


def load_blocklist(path: Path) -> dict[str, list[dict[str, str]]]:
    """Load the exclusion store ``data/blocklist.json`` (channel -> [{id, reason}])."""

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    version = payload.get("schema_version")
    if version is not None and version != EXTRA_SCHEMA_VERSION:
        raise PlanningError(f"{path} has unsupported schema_version {version!r}")
    raw = payload.get("blocklist")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise PlanningError(f"{path} blocklist must be an object")
    return {str(channel): list(entries) for channel, entries in raw.items()}


def index_blocklist(raw: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Index the raw blocklist shape (channel -> [{id, reason}]) for lookup.

    Bare-string entries are accepted with a generic reason; entries index by
    the lowercased channel section name, each id by both its full and its
    bare (vendor-prefix-stripped) form.
    """

    rules: dict[str, dict[str, str]] = {}
    for channel, entries in raw.items():
        if not isinstance(entries, (list, tuple)) or isinstance(entries, str):
            raise PlanningError(f"blocklist.{channel} must be a list")
        indexed: dict[str, str] = {}
        for entry in entries:
            if isinstance(entry, Mapping):
                model_id = str(entry.get("id") or "").strip()
                reason = str(entry.get("reason") or "manual")
            else:
                model_id = str(entry).strip()
                reason = "manual"
            if not model_id:
                continue
            lowered = model_id.lower()
            indexed[lowered] = reason
            indexed.setdefault(lowered.split("/")[-1], reason)
        rules[str(channel).strip().lower()] = indexed
    return rules


def load_cards(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Load the models.dev flat ``vendor/model`` catalog.

    Returns ``(cards, refs)``: ``cards`` indexes every card by bare id;
    ``refs`` keeps the original ``vendor/model`` key so card references can
    point at the catalog entry instead of copying it.
    """

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    cards: dict[str, dict[str, Any]] = {}
    refs: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(value, Mapping):
            continue
        bare_id = str(key).rsplit("/", 1)[-1].strip()
        if bare_id and bare_id not in cards:
            cards[bare_id] = dict(value)
            refs[bare_id] = str(key)
    return cards, refs


def load_arena(path: Path) -> dict[str, dict[str, Any]]:
    """Load the arena snapshot into the lookup shape ``find_best_match`` expects.

    Normalization lives here, not in the collector: keys are the board's raw
    display names and are normalized (idempotent for hand-assigned ids) with
    effort derived from the name itself.  Records are converted to
    ``{rating, organization, effort}`` with ``rating`` mirroring
    ``arena_score``; duplicates keep the higher score, rows without a score
    are skipped.
    """

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    models = payload.get("models")
    if not isinstance(models, Mapping):
        raise PlanningError(f"{path} carries no models object")
    lookup: dict[str, dict[str, Any]] = {}
    for raw_id, raw_value in models.items():
        if not isinstance(raw_value, Mapping):
            continue
        normalized, effort = normalize_arena_name(str(raw_id))
        if not normalized:
            continue
        rating = number(raw_value.get("arena_score", raw_value.get("rating")))
        if rating is None:
            continue
        entry = {
            "rating": rating,
            "organization": raw_value.get("organization", ""),
            "effort": effort,
        }
        old = lookup.get(normalized)
        if old is None or entry["rating"] > old["rating"]:
            lookup[normalized] = entry
    return lookup
