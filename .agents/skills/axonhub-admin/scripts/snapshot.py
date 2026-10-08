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
PROVIDER_CONF_PATH = Path("data/provider_conf.json")


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


def load_provider_conf(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
    """Aggregate the PublicProviderConf catalog into the flat catalog shape.

    The snapshot mirrors the upstream ``all.json`` (``providers`` keyed by
    provider, each with a ``models`` list).  Returns ``(cards, refs,
    canonicals)`` in the same shape :func:`load_cards` uses — ``cards``
    indexed by bare id, ``refs`` keeping the ``vendor/bare`` key — plus
    ``canonicals``: bare id -> canonical bare id where the catalog declares
    a ``canonical_model_id`` pointing elsewhere (per-bare votes resolved to
    the most-voted, ties alphabetical; self-referencing declarations don't
    converge but do vote their vendor as the model's authoritative home).

    Gateways re-expose one model under many providers with drifting
    capability bits, so the card is composed: the base record comes from
    the most-identified vendor (canonical declarations first, then entries
    whose id carries the vendor prefix), and the boolean capability bits
    plus the context/output limits take the majority across candidates —
    a lone gateway's outlier must not become the card the page shows.
    """

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    providers = payload.get("providers")
    if not isinstance(providers, Mapping):
        raise PlanningError(f"{path} carries no providers object")
    entries: dict[str, list[tuple[str, str, Mapping[str, Any]]]] = {}
    canon_votes: dict[str, dict[str, int]] = {}
    identity_votes: dict[str, dict[str, int]] = {}
    for vendor, section in providers.items():
        if not isinstance(section, Mapping):
            continue
        for model in section.get("models") or []:
            if not isinstance(model, Mapping):
                continue
            raw_id = str(model.get("id") or "").strip()
            bare = raw_id.rsplit("/", 1)[-1].strip()
            if not bare:
                continue
            prefix = raw_id[: len(raw_id) - len(bare)].rstrip("/") if "/" in raw_id else ""
            entries.setdefault(bare, []).append((str(vendor), prefix, model))
            canon = str(model.get("canonical_model_id") or "").strip()
            if not canon:
                continue
            canon_vendor, _, canon_bare = canon.rpartition("/")
            canon_bare = (canon_bare or canon).strip()
            canon_vendor = canon_vendor.strip()
            # Two guards before a declaration may converge this id:
            # - the registry's id space is bare lowercase, so a capitalized
            #   canonical form (minimax-m3 -> MiniMax-M3) is a spelling
            #   normalization, not a variant convergence;
            # - convergence may only strip a variant tail (the canonical form
            #   must be a prefix of the id, muse-spark-1.3-contributor ->
            #   muse-spark-1.3).  Renaming declarations (deepseek-v4-flash ->
            #   deepseek-v4-flash-0731, qwen3.8-max -> -preview) rebrand the
            #   model out of the channel id namespace, break Arena matching,
            #   and are the gateway's snapshot naming, not this repo's.
            if (
                canon_bare
                and canon_bare != bare
                and canon_bare == canon_bare.lower()
                and bare.startswith(canon_bare)
            ):
                votes = canon_votes.setdefault(bare, {})
                votes[canon_bare] = votes.get(canon_bare, 0) + 1
            if canon_bare == bare and canon_vendor:
                identified = identity_votes.setdefault(bare, {})
                identified[canon_vendor] = identified.get(canon_vendor, 0) + 1
            elif canon_vendor and canon_bare:
                # A gateway entry declaring another model canonical also
                # vouches for whose catalog the canonical one is.
                identified = identity_votes.setdefault(canon_bare, {})
                identified[canon_vendor] = identified.get(canon_vendor, 0) + 1
    canonicals = {
        bare: sorted(votes.items(), key=lambda item: (-item[1], item[0]))[0][0]
        for bare, votes in canon_votes.items()
    }
    majority_fields = (
        "tool_call",
        "reasoning",
        "temperature",
        "structured_output",
        "attachment",
    )
    cards: dict[str, dict[str, Any]] = {}
    refs: dict[str, str] = {}
    for bare, candidates in entries.items():
        identified = identity_votes.get(bare) or {}
        ranked = sorted(
            candidates,
            key=lambda item: (
                -identified.get(item[0], 0),
                -identified.get(item[1], 0),
                -bool(item[1]),
                -(item[1] == item[0]),
                item[0],
            ),
        )
        vendor, _prefix, base = ranked[0]
        card = dict(base)
        for field in majority_fields:
            votes: dict[Any, int] = {}
            for _candidate_vendor, _candidate_prefix, model in candidates:
                value = model.get(field)
                if isinstance(value, dict):
                    continue
                votes[value] = votes.get(value, 0) + 1
            top = max(votes.items(), key=lambda item: (item[1], str(item[0])))[0] if votes else None
            if top is not None and votes[top] > 1:
                card[field] = top
        for field in ("context", "output"):
            votes = {}
            for _candidate_vendor, _candidate_prefix, model in candidates:
                value = (model.get("limit") or {}).get(field) if isinstance(model.get("limit"), Mapping) else None
                if value is not None:
                    votes[value] = votes.get(value, 0) + 1
            if votes:
                top = sorted(votes.items(), key=lambda item: (-item[1], item[0]))[0][0]
                if votes[top] > 1:
                    limit = dict(card.get("limit") or {})
                    limit[field] = top
                    card["limit"] = limit
        cards[bare] = card
        # The ref carries the attribution (canonical-consensus or self-declared
        # vendor when there is one, the provider section otherwise) so it reads
        # as ``vendor/model`` exactly like a models.dev reference does.
        attribution = (
            max(identified.items(), key=lambda item: (item[1], item[0]))[0]
            if identified
            else (ranked[0][1] or ranked[0][0])
        )
        refs[bare] = f"{attribution}/{bare}"
    return cards, refs, canonicals


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
