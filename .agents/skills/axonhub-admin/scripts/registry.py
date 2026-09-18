#!/usr/bin/env python3
"""Model registry construction, shared by every computing step.

The registry is the deduplicated target catalog behind model-card updates,
channel associations, and Claude mapping: channel records are alias-normalised
into canonical groups, blocklist entries drop excluded records, one winning
channel (highest rp5h) represents each model, variant groups resolve to their
survivor, free models get their quotas re-derived, and every survivor is
triaged against Arena for a mapping standing.  Pure offline computation over
the snapshots; the blocklist is consumed as-is (its derived classes are
materialized by the channel-sync step, never here).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from name_matching import find_best_match, unrecognized_variant_suffix  # noqa: E402
from snapshot import index_blocklist, missing, number  # noqa: E402

VARIANT_SUFFIXES = ("-free", "-contributor")
_VARIANT_PRIORITY = {"-free": 0, "-contributor": 1}
FREE_USAGE_QUOTA_DEFAULT = 60
# Fallback for free models whose channel offers no non-free rp5h to derive from.
FREE_RP5H_DEFAULT = 1000
# Free models with no Arena standing default to this score so free fill always
# has supply.
FREE_DEFAULT_ARENA_SCORE = 1500.0
# A non-free model missing rp5h below this score leaves the registry outright.
RP5H_MISSING_EXCLUDE_THRESHOLD = 1500.0


def is_free_model(record: Mapping[str, Any]) -> bool:
    """Freeness reads the declared prices: input and output both declared
    zero is free; a positive or undeclared headline price is not — an
    undeclared non-free record belongs to the rp5h-missing triage.

    Collection normalizes every channel's freeness into zero cost — the
    watchers transcribe their price tables' ``Free`` wording, the static
    sections declare it by hand — so no flag or id-suffix heuristic
    remains here.  A free model's undeclared cache tiers still price as
    zero on the card (``_merge_channel_cost``).
    """

    cost = record.get("cost") or {}
    return number(cost.get("input")) == 0 and number(cost.get("output")) == 0


def canonical_id(model_id: str, aliases: Mapping[str, str]) -> str:
    """Map a channel-native id onto its registry-canonical form."""

    return aliases.get(model_id, model_id)


def _variant_base(model_id: str) -> str:
    for suffix in VARIANT_SUFFIXES:
        if model_id.lower().endswith(suffix):
            return model_id[: -len(suffix)]
    return model_id


def _variant_rank(model_id: str) -> int:
    lowered = model_id.lower()
    for suffix, rank in _VARIANT_PRIORITY.items():
        if lowered.endswith(suffix):
            return rank
    return len(_VARIANT_PRIORITY)


def _rp5h_of(record: Mapping[str, Any]) -> Optional[float]:
    return number(record.get("rp5h"))


def group_by_canonical(
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    aliases: Mapping[str, str],
) -> dict[str, dict[str, tuple[str, dict[str, Any]]]]:
    """Group channel records by canonical id: canonical -> channel -> (native_id, record)."""

    groups: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
    for channel in sorted(sections):
        for native_id, record in sections[channel].items():
            canonical = canonical_id(native_id, aliases)
            groups.setdefault(canonical, {})[channel] = (native_id, record)
    return groups


def blocklist_reason(
    blocklist: Mapping[str, Mapping[str, str]],
    channel: str,
    native_id: str,
) -> Optional[str]:
    """Return the blocklist reason excluding ``native_id`` on ``channel``.

    Entries are matched by their full id or bare (vendor-prefix-stripped)
    form, case-insensitively: the collected sections carry bare lowercase
    ids while blocklist entries may be spelled as the upstream exposes them.
    """

    rules = blocklist.get(channel.strip().lower()) or {}
    lowered = native_id.strip().lower()
    bare = lowered.split("/")[-1]
    for key in (lowered, bare):
        reason = rules.get(key)
        if reason:
            return reason
    return None


def _pick_channel(members: Mapping[str, tuple[str, dict[str, Any]]]) -> str:
    """Cross-channel dedupe: highest rp5h wins; null loses to a value; ties
    (including both null) keep the alphabetically first channel."""

    winner: Optional[str] = None
    winner_rp5h: Optional[float] = None
    for channel in sorted(members):
        rp5h = _rp5h_of(members[channel][1])
        if winner is None:
            winner, winner_rp5h = channel, rp5h
            continue
        if (winner_rp5h is None and rp5h is not None) or (
            winner_rp5h is not None and rp5h is not None and rp5h > winner_rp5h
        ):
            winner, winner_rp5h = channel, rp5h
    assert winner is not None
    return winner


def dedupe_registry(
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    aliases: Mapping[str, str],
    warnings: list[dict[str, Any]],
    blocklist: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, Any]]:
    """Build the registry: one winning record per canonical id.

    Excluded records never represent their model: a ``blocklist.<channel>``
    entry is the single exclusion mechanism, carrying every class — human
    decisions (``manual``/``tier``/``retired``) plus the channel-sync
    materialized derived ones (``speed:``/``lowscore:``).  Among the rest
    the highest-rp5h channel wins (duplicate warning).  Each entry carries
    every active channel's native id and rp5h — the serving facts the
    channel-priority associations rank.
    """

    registry: dict[str, dict[str, Any]] = {}
    for canonical, members in sorted(group_by_canonical(sections, aliases).items()):
        active: dict[str, tuple[str, dict[str, Any]]] = {}
        for channel, (native_id, record) in members.items():
            reason = blocklist_reason(blocklist, channel, native_id)
            if reason:
                warnings.append({"type": "manual_excluded", "model": native_id, "reason": reason})
                continue
            active[channel] = (native_id, record)
        if not active:
            continue
        winner_channel = _pick_channel(active)
        native_id, record = active[winner_channel]
        if len(active) > 1:
            warnings.append(
                {
                    "type": "duplicate_model_across_sources",
                    "model": canonical,
                    "kept": winner_channel,
                    "dropped": ", ".join(sorted(c for c in active if c != winner_channel)),
                }
            )
        registry[canonical] = {
            "channel": winner_channel,
            "native_id": native_id,
            "record": record,
            "channel_aliases": {
                channel: nid for channel, (nid, _rec) in active.items() if nid != canonical
            },
            # Every active serving channel with its declared rp5h; drives the
            # channel-priority association plan (ADR 0016).
            "serving": {channel: _rp5h_of(rec) for channel, (_nid, rec) in active.items()},
        }
    return registry


def supersede_variants(
    registry: dict[str, dict[str, Any]], warnings: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Within a base-model variant group, -free beats -contributor beats plain.

    Superseded variants leave the registry with a warning; the surviving
    variant keeps its own id — it is what the winning channel actually
    exposes, and free detection relies on the suffix.
    """

    groups: dict[str, list[str]] = {}
    for canonical in registry:
        groups.setdefault(_variant_base(canonical), []).append(canonical)
    for base, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda m: (_variant_rank(m), m))
        survivor = ranked[0]
        for member in ranked[1:]:
            del registry[member]
            warnings.append({"type": "variant_superseded", "model": member, "replaced_by": survivor})
    return registry


def fill_free_records(
    registry: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    """Re-derive free-model quotas per owning channel, in place.

    rp5h is recomputed from the channel's largest non-free rp5h even when the
    collector pre-filled it, so the rule has exactly one owning channel.
    """

    per_channel: dict[str, dict[str, dict[str, Any]]] = {}
    for canonical, entry in registry.items():
        per_channel.setdefault(entry["channel"], {})[canonical] = entry["record"]
    for channel, records in sorted(per_channel.items()):
        non_free = [
            number(record.get("rp5h"), 0.0) or 0.0
            for model_id, record in records.items()
            if not is_free_model(record)
        ]
        max_rp5h = max(non_free) if non_free else 0.0
        if max_rp5h <= 0:
            max_rp5h = FREE_RP5H_DEFAULT
        for model_id, record in sorted(records.items()):
            if not is_free_model(record):
                continue
            filled: list[str] = []
            derived = int(max_rp5h) if float(max_rp5h).is_integer() else max_rp5h
            if number(record.get("rp5h")) != derived:
                record["rp5h"] = derived
                filled.append("rp5h")
            if missing(record.get("usage_quota")):
                record["usage_quota"] = FREE_USAGE_QUOTA_DEFAULT
                filled.append("usage_quota")
            if filled:
                warnings.append(
                    {
                        "type": "free_default_filled",
                        "model": model_id,
                        "fields": filled,
                        "provider": channel,
                    }
                )


def build_registry(
    *,
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    aliases: Mapping[str, str],
    blocklist_raw: Mapping[str, Any],
    arena_models: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Run the full registry pipeline over the snapshots.

    Returns ``{registry, candidates, warnings, ineligible}``.  The blocklist
    is consumed as-is: its derived ``speed:``/``lowscore:`` classes are
    materialized by the channel-sync step, never here.  Candidates carry the
    Arena standing each model needs for mapping — free models default to
    1500 when the board never listed them, non-free models without a
    legitimate score stay unscored and out of the mapping pool (ADR 0015),
    and non-free models missing rp5h leave the registry entirely below the
    review threshold.
    """

    warnings: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []
    blocklist = index_blocklist(blocklist_raw)

    registry = dedupe_registry(sections, aliases, warnings, blocklist)
    registry = supersede_variants(registry, warnings)
    fill_free_records(registry, warnings)

    candidates: list[dict[str, Any]] = []
    rp5h_excluded: list[str] = []
    for canonical in sorted(registry):
        entry = registry[canonical]
        record = entry["record"]
        is_free = is_free_model(record)
        match, match_type = find_best_match(canonical, dict(arena_models))
        arena_score = number((match or {}).get("rating"))
        if match_type == "no_match":
            suffix = unrecognized_variant_suffix(canonical)
            if suffix:
                warnings.append(
                    {
                        "type": "unrecognized_variant_suffix",
                        "model": canonical,
                        "suffix": suffix,
                    }
                )
            if is_free:
                # Free reachability: free fill must stay able to pair every
                # request even when the board never listed the model.
                arena_score = FREE_DEFAULT_ARENA_SCORE
                match_type = "free_defaulted"
                warnings.append(
                    {"type": "arena_defaulted", "model": canonical, "score": FREE_DEFAULT_ARENA_SCORE}
                )
            else:
                # No invented standing: an unlisted non-free model carries no
                # score and stays out of the mapping pool (ADR 0015).
                arena_score = None
                match_type = "arena_missing"
                warnings.append({"type": "arena_missing", "model": canonical})
        elif match_type in ("version_downgrade", "prefix_match"):
            # Borrowed scores put another version's or a name family's
            # standing on this id; only same-model variant suffixes inherit
            # (ADR 0015).
            arena_score = None
            warnings.append(
                {"type": "arena_borrowed_rejected", "model": canonical, "match_type": match_type}
            )
        rp5h = number(record.get("rp5h"))
        if not is_free and rp5h is None:
            reference = arena_score if arena_score is not None else 0.0
            if reference < RP5H_MISSING_EXCLUDE_THRESHOLD:
                warnings.append(
                    {
                        "type": "rp5h_missing_excluded",
                        "model": canonical,
                        "arena_score": reference,
                    }
                )
                rp5h_excluded.append(canonical)
                continue
            warnings.append(
                {
                    "type": "rp5h_missing_review",
                    "model": canonical,
                    "arena_score": reference,
                }
            )
            ineligible.append({"modelID": canonical, "reason": "missing_rp5h"})
        candidates.append(
            {
                "model_id": canonical,
                "channel": entry["channel"],
                "free": is_free,
                "rp5h": rp5h,
                "arena_score": arena_score,
                "match_type": match_type,
            }
        )
    for canonical in rp5h_excluded:
        del registry[canonical]

    return {
        "registry": registry,
        "candidates": candidates,
        "warnings": warnings,
        "ineligible": ineligible,
    }
