#!/usr/bin/env python3
"""Offline model-registry planner: dedupe, fill, and map.

Pipeline position: watch-pipeline collects channel-declared facts into
``data/models_extra.json`` (plus ``data/all_models.json`` for public cards and
``data/arena.json`` for quality signals); this script reads those snapshots
and produces everything the AxonHub write path consumes:

1. Dedupe across channels: a model id listed by several channels belongs to
   the channel with the highest rp5h (null loses to a value, ties keep the
   alphabetically first channel); every resolution is reported as a warning.
2. Free-model completion: per owning channel, a free model's rp5h is
   re-derived from the channel's largest non-free rp5h and a missing
   usage_quota becomes 60.
3. Card assembly: public card fields come from ``data/all_models.json``
   (models.dev); channel ``cost`` values win where present.  A model without
   a card is planned with channel-claimed data only and reported.
4. Claude mapping: fixed Claude request models are mapped to the candidate
   pool by the Arena/RP5H/proximity formula (baseline routing included);
   GPT request models are pass-through and never enter the mapping.
5. Outputs: ``models.csv`` (the reviewable mapping suggestion table) and a
   schema-2 catalog plan (per-channel exact ``supportedModels`` plus model
   card targets) for the axonhub-admin interactive write path.

Pure offline planning: no credentials, no network, no AxonHub writes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from csv_io import write_mapping  # noqa: E402
from name_matching import find_best_match, normalize_arena_name  # noqa: E402

PLAN_SCHEMA_VERSION = 2
EXTRA_SCHEMA_VERSION = 1
REMARK_FIELDS = ("rp5h", "usage_quota", "context_threshold", "peak_hours", "retention")
REMOVAL_NOTE = "执行时核验外部引用与外部渠道使用，再删除全局模型对象"
FREE_USAGE_QUOTA_DEFAULT = 60
DEFAULT_WEIGHTS = {
    "score": 0.35,
    "rp5h": 0.30,
    "proximity": 0.35,
    "penalty_k": 0.2,
    "upgrade_bonus": 0.1,
}


class PlanningError(RuntimeError):
    """A user-actionable planning failure."""


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or isinstance(value, bool) or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number):
        return default
    return number


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == "-"


def _report_item(code: str, message: str, **details: Any) -> dict:
    item = {"code": code, "message": message}
    item.update(details)
    return item


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanningError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlanningError(f"{path} is not valid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Loading


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


def load_cards(path: Path) -> dict[str, dict[str, Any]]:
    """Load the models.dev flat ``vendor/model`` catalog indexed by bare id."""

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    cards: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(value, Mapping):
            continue
        bare_id = str(key).rsplit("/", 1)[-1].strip()
        if bare_id and bare_id not in cards:
            cards[bare_id] = dict(value)
    return cards


def load_arena(path: Path) -> dict[str, dict[str, Any]]:
    """Load the arena snapshot into the lookup shape ``find_best_match`` expects.

    Keys are re-normalized (idempotent for watcher output), records are
    converted to ``{rank, rating, context, organization, effort}`` with
    ``rating`` mirroring ``arena_score``, and duplicates keep the higher
    score.  Rows without a score are skipped.
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
        normalized, _ = normalize_arena_name(str(raw_id))
        if not normalized:
            continue
        rating = _number(raw_value.get("arena_score", raw_value.get("rating")))
        if rating is None:
            continue
        entry = {
            "rank": int(_number(raw_value.get("arena_rank", raw_value.get("rank")), 0) or 0),
            "rating": rating,
            "context": raw_value.get("arena_context", raw_value.get("context", "-")),
            "organization": raw_value.get("organization", ""),
            "effort": raw_value.get("effort"),
        }
        old = lookup.get(normalized)
        if old is None or entry["rating"] > old["rating"]:
            lookup[normalized] = entry
    return lookup


def load_request_models(path: Path) -> list[dict[str, Any]]:
    """Load enabled Claude request models; GPT entries are pass-through."""

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    models = payload.get("models")
    if not isinstance(models, list):
        raise PlanningError(f"{path} carries no models list")
    requests: list[dict[str, Any]] = []
    for entry in models:
        if not isinstance(entry, Mapping):
            raise PlanningError(f"{path} has a non-object model entry")
        model_id = str(entry.get("model_id") or "").strip()
        if not model_id:
            raise PlanningError(f"{path} has a model entry without model_id")
        if entry.get("enabled") is False:
            continue
        if extract_series(model_id) != "claude":
            continue  # GPT series: pass-through, not part of the mapping.
        requests.append(dict(entry))
    return requests


def load_decisions(path: Path) -> dict[str, Any]:
    """Load model decisions: scope, per-model excludes/supplements, overrides."""

    payload = load_json(path)
    if not isinstance(payload, Mapping):
        raise PlanningError(f"{path} is not a JSON object")
    scope = _as_dict(payload.get("scope"))
    raw_channels = _as_dict(scope.get("channels"))
    channels = {
        str(provider): str(channel)
        for provider, channel in raw_channels.items()
        if str(channel).strip()
    }
    if not channels:
        raise PlanningError(f"{path} scope.channels must map providers to channels")
    excluded: dict[str, dict[str, str]] = {}
    supplements: dict[str, dict[str, dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for entry in payload.get("models") or []:
        if not isinstance(entry, Mapping):
            raise PlanningError(f"{path} has a non-object decision entry")
        provider = str(entry.get("provider") or "").strip()
        model_id = str(entry.get("model_id") or "").strip()
        action = str(entry.get("action") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        if not provider or not model_id or not action or not reason:
            raise PlanningError(f"{path} decision entries need provider/model_id/action/reason")
        key = (provider, model_id)
        if key in seen:
            raise PlanningError(f"{path} has duplicate decisions for {provider}/{model_id}")
        seen.add(key)
        if action == "exclude":
            excluded.setdefault(provider, {})[model_id] = reason
        elif action == "supplement":
            fields = _as_dict(entry.get("fields"))
            bad = sorted(set(fields) - set(REMARK_FIELDS))
            if bad:
                raise PlanningError(f"{path} supplement for {model_id} has unknown fields {bad}")
            supplements.setdefault(provider, {})[model_id] = fields
        else:
            raise PlanningError(f"{path} has unknown decision action {action!r}")
    overrides: dict[str, dict[str, str]] = {}
    for entry in payload.get("mapping_overrides") or []:
        if not isinstance(entry, Mapping):
            raise PlanningError(f"{path} has a non-object mapping override")
        request_model = str(entry.get("request_model") or "").strip()
        target_model = str(entry.get("target_model") or "").strip()
        if not request_model or not target_model:
            raise PlanningError(f"{path} mapping overrides need request_model/target_model")
        overrides[request_model] = {
            "target_model": target_model,
            "reason": str(entry.get("reason") or ""),
        }
    return {"channels": channels, "excluded": excluded, "supplements": supplements, "overrides": overrides}


# ---------------------------------------------------------------------------
# Dedupe + completion

VARIANT_SUFFIXES = ("-free", "-contributor")
_VARIANT_PRIORITY = {"-free": 0, "-contributor": 1}
DEFAULT_ARENA_SCORE = 1500.0
FREE_DEFAULT_ARENA_SCORE = 1500.0
RP5H_MISSING_EXCLUDE_THRESHOLD = 1500.0


def _rp5h_of(record: Mapping[str, Any]) -> Optional[float]:
    return _number(record.get("rp5h"))


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
) -> dict[str, dict[str, Any]]:
    """Build the registry: one winning record per canonical id.

    Records the collector stamped with ``exclude`` never represent their
    model; among the rest the highest-rp5h channel wins (duplicate warning).
    Each entry carries the channel's native id plus the alias map needed to
    route channel-exposed ids back to the canonical model.
    """

    registry: dict[str, dict[str, Any]] = {}
    for canonical, members in sorted(group_by_canonical(sections, aliases).items()):
        active: dict[str, tuple[str, dict[str, Any]]] = {}
        for channel, (native_id, record) in members.items():
            reason = record.get("exclude")
            if reason:
                warnings.append(
                    {"type": "collector_excluded", "model": native_id, "reason": reason}
                )
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
            _number(record.get("rp5h"), 0.0) or 0.0
            for model_id, record in records.items()
            if "free" not in model_id.lower()
        ]
        max_rp5h = max(non_free) if non_free else 0.0
        for model_id, record in sorted(records.items()):
            if "free" not in model_id.lower():
                continue
            filled: list[str] = []
            derived = int(max_rp5h) if float(max_rp5h).is_integer() else max_rp5h
            if _number(record.get("rp5h")) != derived:
                record["rp5h"] = derived
                filled.append("rp5h")
            if _missing(record.get("usage_quota")):
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


# ---------------------------------------------------------------------------
# Card + remark assembly (plan side)


def _cost_from_model(model: Mapping[str, Any]) -> dict[str, Any]:
    raw_cost = _as_dict(model.get("cost"))
    nested_cache = _as_dict(raw_cost.get("cache"))
    return {
        "input": raw_cost.get("input", 0),
        "output": raw_cost.get("output", 0),
        "cacheRead": raw_cost.get("cache_read", raw_cost.get("cacheRead", nested_cache.get("read", 0))),
        "cacheWrite": raw_cost.get("cache_write", raw_cost.get("cacheWrite", nested_cache.get("write", 0))),
    }


def _merge_channel_cost(card: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """Start from the card cost and overwrite with non-null channel values."""

    cost = _cost_from_model(card)
    channel_cost = _as_dict(record.get("cost"))
    aliases = {"input": "input", "output": "output", "cache_read": "cacheRead", "cacheRead": "cacheRead",
               "cache_write": "cacheWrite", "cacheWrite": "cacheWrite"}
    for key, value in channel_cost.items():
        target = aliases.get(key)
        if target is not None and value is not None:
            cost[target] = value
    return cost


def model_card(model: Mapping[str, Any]) -> dict[str, Any]:
    """Map source capabilities to AxonHub's modelCard shape."""

    modalities = _as_dict(model.get("modalities"))
    limit = _as_dict(model.get("limit"))
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


# ---------------------------------------------------------------------------
# Claude mapping


def extract_series(model_id: str) -> str:
    """Return the Claude/GPT request family used for baseline selection."""

    match = re.match(r"^(claude|gpt)(?:-|$)", str(model_id).lower())
    return match.group(1) if match else ""


def find_baseline_model(models: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """Find the lowest scored request model in a family."""

    scored = []
    for model in models:
        score = _number(model.get("arena_score"))
        model_id = str(model.get("model_id") or "").strip()
        if score is not None and model_id:
            scored.append((score, model_id))
    if not scored:
        return None
    return min(scored, key=lambda item: (item[0], item[1]))[1]


def score_match(
    request_score: float,
    candidate: Mapping[str, Any],
    max_values: Mapping[str, float],
    weights: Optional[Mapping[str, float]] = None,
) -> float:
    """Score one candidate using the agreed Arena/RP5H/proximity formula."""

    selected = dict(DEFAULT_WEIGHTS)
    if weights:
        selected.update(weights)
    candidate_score = _number(candidate.get("arena_score"), 0.0) or 0.0
    max_score = _number(max_values.get("max_score"), 1.0) or 1.0
    max_rp5h = _number(max_values.get("max_rp5h"), 1.0) or 1.0
    max_score_diff = _number(max_values.get("max_score_diff"), 0.0) or 0.0
    rp5h = _number(candidate.get("rp5h"), 0.0) or 0.0

    score_component = candidate_score / max_score if max_score else 0.0
    if max_rp5h > 0:
        rp5h_component = math.log1p(max(rp5h, 0.0)) / math.log1p(max_rp5h)
    else:
        rp5h_component = 0.0
    if max_score_diff == 0:
        proximity = 1.0
    else:
        proximity = 1.0 - abs(candidate_score - request_score) / max_score_diff

    penalty = 0.0
    if candidate_score < request_score and max_score_diff:
        penalty = selected["penalty_k"] * (request_score - candidate_score) / max_score_diff
    upgrade = selected["upgrade_bonus"] if candidate_score > request_score else 0.0

    return (
        selected["score"] * score_component
        + selected["rp5h"] * rp5h_component
        + selected["proximity"] * proximity
        - penalty
        + upgrade
    )


def compute_mapping_for_request_model(
    request_score: float,
    candidates: Sequence[Mapping[str, Any]],
    weights: Optional[Mapping[str, float]] = None,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Choose the highest-scoring eligible candidate for one request model."""

    eligible = []
    for candidate in candidates:
        model_id = str(candidate.get("model_id") or "").strip()
        score = _number(candidate.get("arena_score"))
        rp5h = _number(candidate.get("rp5h"))
        if model_id and score is not None and rp5h is not None:
            eligible.append(candidate)
    if not eligible:
        return None, None

    scores = [_number(item.get("arena_score"), 0.0) or 0.0 for item in eligible]
    max_values = {
        "max_score": max(scores) or 1.0,
        "max_rp5h": max(_number(item.get("rp5h"), 0.0) or 0.0 for item in eligible) or 1.0,
        "max_score_diff": (max(scores) - min(scores)) if len(scores) > 1 else 0.0,
    }
    best_score, best_id, best_item = min(
        (
            (score_match(request_score, item, max_values, weights), str(item["model_id"]), item)
            for item in eligible
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return best_id, {"target": best_id, "score": best_score, "candidate": best_item, "max_values": max_values}


def _confidence(match_type: str) -> str:
    if match_type == "direct_match":
        return "high"
    if match_type in ("contributor_suffix", "version_downgrade", "free_inherited"):
        return "medium"
    if match_type in ("prefix_match", "free_default"):
        return "low"
    return "none"


# ---------------------------------------------------------------------------
# Main pipeline


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


def build_plan(
    *,
    extra_path: Path,
    cards_path: Path,
    arena_path: Path,
    request_models_path: Path,
    decisions_path: Path,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Load the snapshots and run the planning pipeline."""

    sections = load_extra_sections(extra_path)
    aliases = load_extra_aliases(extra_path)
    cards = load_cards(cards_path)
    arena_models = load_arena(arena_path)
    request_models = load_request_models(request_models_path)
    decisions = load_decisions(decisions_path)
    return plan_from(
        sections,
        aliases=aliases,
        cards=cards,
        arena_models=arena_models,
        request_models=request_models,
        decisions=decisions,
    )


def plan_from(
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    *,
    aliases: Mapping[str, str] | None = None,
    cards: Mapping[str, Mapping[str, Any]],
    arena_models: Mapping[str, Mapping[str, Any]],
    request_models: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Run the offline planning pipeline; return (csv rows, plan)."""

    aliases = aliases or {}
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []

    unknown = sorted(set(sections) - set(decisions["channels"]))
    if unknown:
        raise PlanningError(
            f"models_extra channels {unknown} are not in scope.channels {sorted(decisions['channels'])}"
        )

    # Registry: alias-normalised groups, collector excludes dropped, one
    # winning channel per canonical id, then variant groups resolved.
    registry = dedupe_registry(sections, aliases, warnings)
    registry = supersede_variants(registry, warnings)
    fill_free_records(registry, warnings)

    def _find(model_id: str, provider: str | None = None) -> Optional[str]:
        for key, entry in registry.items():
            if provider is not None and entry["channel"] != provider:
                continue
            if model_id in (key, entry["native_id"]):
                return key
        return None

    for provider, per_provider in sorted(decisions["excluded"].items()):
        for model_id in sorted(per_provider):
            key = _find(model_id, provider)
            if key is not None:
                del registry[key]
    excluded_rows = [
        {"modelID": model_id, "reason": reason, "note": REMOVAL_NOTE}
        for provider, per_provider in sorted(decisions["excluded"].items())
        for model_id, reason in sorted(per_provider.items())
    ]
    for provider, per_provider in sorted(decisions["supplements"].items()):
        for model_id, fields in sorted(per_provider.items()):
            key = _find(model_id, provider)
            if key is not None:
                registry[key]["record"].update(fields)
            else:
                warnings.append({"type": "supplement_unknown_model", "model": model_id})

    # Candidate pool: arena match (no match defaults to 1500 for non-free
    # models so beta models stay reviewable), then rp5h-missing triage.
    candidates: list[dict[str, Any]] = []
    rp5h_excluded: list[str] = []
    for canonical in sorted(registry):
        entry = registry[canonical]
        record = entry["record"]
        is_free = "free" in canonical.lower()
        # is_free=False keeps the free_default 0-score fallback out of the
        # way: free models inherit their base variant's score instead.
        match, match_type = find_best_match(canonical, dict(arena_models))
        arena_score = _number((match or {}).get("rating"))
        if match_type == "no_match" and is_free:
            base = _variant_base(canonical)
            base_entry = arena_models.get(base)
            if isinstance(base_entry, Mapping) and _number(base_entry.get("rating")) is not None:
                arena_score = _number(base_entry.get("rating"))
                match_type = "free_inherited"
                warnings.append(
                    {"type": "arena_inherited", "model": canonical, "from": base, "score": arena_score}
                )
            else:
                arena_score = FREE_DEFAULT_ARENA_SCORE
                match_type = "free_defaulted"
                warnings.append(
                    {"type": "arena_defaulted", "model": canonical, "score": FREE_DEFAULT_ARENA_SCORE}
                )
        elif match_type == "no_match":
            arena_score = DEFAULT_ARENA_SCORE
            warnings.append(
                {"type": "arena_defaulted", "model": canonical, "score": DEFAULT_ARENA_SCORE}
            )
        elif match_type not in ("direct_match", "free_default"):
            warnings.append(
                {"type": "candidate_arena_fallback", "model": canonical, "match_type": match_type}
            )
        rp5h = _number(record.get("rp5h"))
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
                "rp5h": rp5h,
                "arena_score": arena_score,
                "match_type": match_type,
            }
        )
    for canonical in rp5h_excluded:
        del registry[canonical]

    # Claude request mapping (baseline routing + formula + overrides).
    mappings: list[dict[str, Any]] = []
    request_rows: list[dict[str, str]] = []
    scored_requests: list[dict[str, Any]] = []
    for request in request_models:
        model_id = str(request.get("model_id") or "").strip()
        pinned = _number(request.get("arena_score"))
        if pinned is not None:
            score = pinned
        else:
            arena_key = str(request.get("arena_model_id") or model_id)
            match, match_type = find_best_match(arena_key, dict(arena_models))
            score = _number((match or {}).get("rating"))
            if match is None or score is None:
                errors.append(
                    _report_item(
                        "request_arena_missing",
                        f"request model {model_id} has no arena score",
                        model=model_id,
                    )
                )
                continue
            if match_type != "direct_match":
                warnings.append(
                    {"type": "request_arena_fallback", "model": model_id, "match_type": match_type}
                )
        scored_requests.append({**request, "arena_score": score})
    if not scored_requests:
        warnings.append({"type": "empty_claude_series", "message": "no enabled claude-* request models"})

    # Mapping order: (1) every request is scored by the formula over
    # non-free candidates; (2) free fill — the free pool (ascending) is
    # paired with the requests (ascending arena score), replacing the
    # lowest-scored requests' formula targets; (3) overrides apply last.
    scored_requests.sort(key=lambda item: (float(item["arena_score"]), str(item["model_id"])))
    free_candidates = sorted(
        (c for c in candidates if "free" in c["model_id"].lower()),
        key=lambda c: ((c["arena_score"] if c["arena_score"] is not None else 0.0), c["model_id"]),
    )
    non_free_candidates = [c for c in candidates if "free" not in c["model_id"].lower()]

    resolved: dict[str, tuple[Optional[str], str]] = {}
    for request in scored_requests:
        score = float(request["arena_score"])
        target, _details = compute_mapping_for_request_model(score, non_free_candidates)
        match_type = next(
            (c["match_type"] for c in non_free_candidates if c["model_id"] == target), ""
        )
        resolved[str(request["model_id"])] = (target, _confidence(match_type))

    free_supply = iter(free_candidates)
    for request in scored_requests:
        free_entry = next(free_supply, None)
        if free_entry is None:
            break
        resolved[str(request["model_id"])] = (free_entry["model_id"], "free_fill")

    for request in scored_requests:
        model_id = str(request["model_id"])
        score = float(request["arena_score"])
        target, confidence = resolved[model_id]
        override = decisions["overrides"].get(model_id)
        if override:
            target = override["target_model"]
            confidence = "override"
        if target is not None and not any(c["model_id"] == target for c in candidates):
            errors.append(
                _report_item(
                    "invalid_mapping_override_target",
                    f"target {target} for {model_id} is not in the candidate pool",
                    model=model_id,
                    target=target,
                )
            )
            target = None
        if target is None:
            errors.append(
                _report_item(
                    "request_without_target",
                    f"request model {model_id} has no eligible target",
                    model=model_id,
                )
            )
        mappings.append(
            {
                "request_model": model_id,
                "suggested_target": target,
                "arena_score": score,
                "match_confidence": confidence,
            }
        )
        request_rows.append(
            {
                "model_id": model_id,
                "role": "request",
                "arena_score": f"{score:g}",
                "rp5h": "",
                "mapping": target or "",
            }
        )

    # Candidate CSV rows (the reviewable pool, sorted like the old workspace).
    candidate_rows = [
        {
            "model_id": candidate["model_id"],
            "role": "candidate",
            "arena_score": f"{candidate['arena_score']:g}" if candidate["arena_score"] is not None else "",
            "rp5h": f"{candidate['rp5h']:g}" if candidate["rp5h"] is not None else "",
            "mapping": "",
        }
        for candidate in sorted(
            candidates,
            key=lambda item: (-(item["arena_score"] if item["arena_score"] is not None else -1.0), item["model_id"]),
        )
    ]

    # Plan: channel lists keep native ids (routing); global models use the
    # canonical id with channelAliases describing per-channel exposure.
    provider_channels = decisions["channels"]
    channels: dict[str, dict[str, Any]] = {
        provider_channels[provider]: {"supportedModels": []}
        for provider in sorted(sections)
    }
    for canonical in sorted(registry):
        entry = registry[canonical]
        axon_channel = provider_channels[entry["channel"]]
        channels.setdefault(axon_channel, {"supportedModels": []})
        if entry["native_id"] not in channels[axon_channel]["supportedModels"]:
            channels[axon_channel]["supportedModels"].append(entry["native_id"])
    for channel_node in channels.values():
        channel_node["supportedModels"] = sorted(channel_node["supportedModels"])

    plan_models: list[dict[str, Any]] = []
    for canonical in sorted(registry):
        entry = registry[canonical]
        record = entry["record"]
        axon_channel = provider_channels[entry["channel"]]
        card = cards.get(canonical) or cards.get(entry["native_id"])
        if card is None:
            warnings.append({"type": "card_missing", "model": canonical, "provider": entry["channel"]})
        merged = dict(card or {})
        merged["cost"] = _merge_channel_cost(card or {}, record)
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
            "input": {
                "modelID": canonical,
                "name": str(merged.get("name") or record.get("name") or canonical),
                "developer": developer,
                "type": "chat",
                "icon": icon,
                "group": group,
                "modelCard": model_card(merged),
                "remark": remark_json(remark),
            },
        }
        if entry["channel_aliases"]:
            model_entry["channelAliases"] = dict(sorted(entry["channel_aliases"].items()))
        plan_models.append(model_entry)

    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "providers": dict(sorted(provider_channels.items())),
        "channels": dict(sorted(channels.items())),
        "models": plan_models,
        "removals": excluded_rows,
        "warnings": warnings,
        "report": {
            "errors": errors,
            "ineligible": ineligible,
            "mappings": mappings,
            "counts": {
                "models": len(plan_models),
                "channels": len(channels),
                "candidates": len(candidate_rows),
                "requests": len(request_rows),
                "removals": len(excluded_rows),
                "warnings": len(warnings),
                "errors": len(errors),
            },
        },
    }
    return candidate_rows + request_rows, plan


def render_report(plan: Mapping[str, Any]) -> str:
    """Human-readable summary of the planning run."""

    report = plan["report"]
    lines = [json.dumps(report["counts"], ensure_ascii=False, sort_keys=True)]
    for item in report["errors"]:
        lines.append(f"ERROR [{item['code']}] {item['message']}")
    for warning in plan["warnings"]:
        fields = " ".join(f"{key}={warning[key]!r}" for key in warning if key != "type")
        lines.append(f"WARNING [{warning['type']}] {fields}")
    for item in report["ineligible"]:
        lines.append(f"INELIGIBLE {item['modelID']}: {item['reason']}")
    for mapping in report["mappings"]:
        lines.append(
            f"MAPPING {mapping['request_model']} -> {mapping['suggested_target']} "
            f"(arena {mapping['arena_score']:g}, {mapping['match_confidence']})"
        )
    return "\n".join(lines)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dedupe models_extra across channels and plan cards, lists, and Claude mappings"
    )
    parser.add_argument("--extra", type=Path, default=Path("data/models_extra.json"))
    parser.add_argument("--cards", type=Path, default=Path("data/all_models.json"))
    parser.add_argument("--arena", type=Path, default=Path("data/arena.json"))
    parser.add_argument("--request-models", type=Path, default=Path("config/request-models.json"))
    parser.add_argument("--model-decisions", type=Path, default=Path("config/model-decisions.json"))
    parser.add_argument("--csv-output", type=Path, default=Path("models.csv"))
    parser.add_argument("--plan-output", type=Path, default=None)
    parser.add_argument("--fail-on-errors", action="store_true")
    args = parser.parse_args(argv)

    try:
        rows, report = build_plan(
            extra_path=args.extra,
            cards_path=args.cards,
            arena_path=args.arena,
            request_models_path=args.request_models,
            decisions_path=args.model_decisions,
        )
    except PlanningError as exc:
        print(f"models-mapping: {exc}", file=sys.stderr)
        return 1

    write_mapping(args.csv_output, rows)
    if args.plan_output:
        write_json(args.plan_output, report)
    print(render_report(report))
    if args.fail_on_errors and report["report"]["errors"]:
        print(
            f"models-mapping: {len(report['report']['errors'])} blocking error(s); "
            "plan is for inspection only",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
