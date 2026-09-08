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


def _rp5h_of(record: Mapping[str, Any]) -> Optional[float]:
    return _number(record.get("rp5h"))


def dedupe_channels(
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    warnings: list[dict[str, Any]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Keep one channel per model id: the one with the highest rp5h.

    A null rp5h loses to a value; ties (including both null) keep the
    alphabetically first channel, so the result is deterministic.
    """

    chosen: dict[str, tuple[str, dict[str, Any]]] = {}
    for channel in sorted(sections):
        for model_id, record in sections[channel].items():
            if model_id not in chosen:
                chosen[model_id] = (channel, record)
                continue
            kept_channel, kept_record = chosen[model_id]
            kept_rp5h, candidate_rp5h = _rp5h_of(kept_record), _rp5h_of(record)
            if (kept_rp5h is None and candidate_rp5h is not None) or (
                kept_rp5h is not None
                and candidate_rp5h is not None
                and candidate_rp5h > kept_rp5h
            ):
                winner = channel
            else:
                winner = kept_channel
            warnings.append(
                {
                    "type": "duplicate_model_across_sources",
                    "model": model_id,
                    "kept": winner,
                    "dropped": channel if winner == kept_channel else kept_channel,
                }
            )
            if winner == channel:
                chosen[model_id] = (channel, record)
    return chosen


def fill_free_records(
    owned: Mapping[str, Mapping[str, dict[str, Any]]],
    warnings: list[dict[str, Any]],
) -> None:
    """Re-derive free-model quotas per owning channel, in place.

    rp5h is recomputed from the channel's largest non-free rp5h even when the
    collector pre-filled it, so the rule has exactly one owning channel.
    """

    for channel, records in sorted(owned.items()):
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
    if match_type in ("contributor_suffix", "version_downgrade"):
        return "medium"
    if match_type in ("prefix_match", "free_default"):
        return "low"
    return "none"


# ---------------------------------------------------------------------------
# Main pipeline


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
    cards = load_cards(cards_path)
    arena_models = load_arena(arena_path)
    request_models = load_request_models(request_models_path)
    decisions = load_decisions(decisions_path)
    return plan_from(
        sections,
        cards=cards,
        arena_models=arena_models,
        request_models=request_models,
        decisions=decisions,
    )


def plan_from(
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    *,
    cards: Mapping[str, Mapping[str, Any]],
    arena_models: Mapping[str, Mapping[str, Any]],
    request_models: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Run the offline planning pipeline; return (csv rows, plan)."""

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []

    unknown = sorted(set(sections) - set(decisions["channels"]))
    if unknown:
        raise PlanningError(
            f"models_extra channels {unknown} are not in scope.channels {sorted(decisions['channels'])}"
        )

    chosen = dedupe_channels(sections, warnings)
    owned: dict[str, dict[str, dict[str, Any]]] = {channel: {} for channel in sections}
    for model_id, (channel, record) in sorted(chosen.items()):
        owned[channel][model_id] = record
    fill_free_records(owned, warnings)

    for provider, per_provider in sorted(decisions["excluded"].items()):
        for model_id in sorted(per_provider):
            if provider in owned and model_id in owned[provider]:
                del owned[provider][model_id]
    excluded_rows = [
        {"modelID": model_id, "reason": reason, "note": REMOVAL_NOTE}
        for provider, per_provider in sorted(decisions["excluded"].items())
        for model_id, reason in sorted(per_provider.items())
    ]
    for provider, per_provider in sorted(decisions["supplements"].items()):
        for model_id, fields in sorted(per_provider.items()):
            if provider in owned and model_id in owned[provider]:
                owned[provider][model_id].update(fields)
            else:
                warnings.append({"type": "supplement_unknown_model", "model": model_id})

    # Candidate pool with arena matches (every surviving model, any channel).
    candidates: list[dict[str, Any]] = []
    for channel in sorted(owned):
        for model_id in sorted(owned[channel]):
            record = owned[channel][model_id]
            is_free = "free" in model_id.lower()
            match, match_type = find_best_match(model_id, dict(arena_models), is_free=is_free)
            candidate = {
                "model_id": model_id,
                "channel": channel,
                "rp5h": _number(record.get("rp5h")),
                "arena_score": _number((match or {}).get("rating")),
                "match_type": match_type,
            }
            candidates.append(candidate)
            if match is None:
                ineligible.append({"modelID": model_id, "reason": "missing_arena"})
            elif match_type not in ("direct_match",):
                warnings.append(
                    {
                        "type": "candidate_arena_fallback",
                        "model": model_id,
                        "match_type": match_type,
                    }
                )
            if candidate["rp5h"] is None:
                ineligible.append({"modelID": model_id, "reason": "missing_rp5h"})

    # Claude request mapping (baseline routing + formula + overrides).
    mappings: list[dict[str, Any]] = []
    request_rows: list[dict[str, str]] = []
    scored_requests: list[dict[str, Any]] = []
    for request in request_models:
        model_id = str(request.get("model_id") or "").strip()
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

    eligible_pool = [
        candidate
        for candidate in candidates
        if candidate["arena_score"] is not None and candidate["rp5h"] is not None
    ]
    free_pool = [candidate for candidate in eligible_pool if "free" in candidate["model_id"].lower()]
    baseline_id = find_baseline_model(scored_requests)
    for request in sorted(scored_requests, key=lambda item: str(item["model_id"])):
        model_id = str(request["model_id"])
        score = float(request["arena_score"])
        if baseline_id is not None and model_id == baseline_id:
            pool = free_pool or eligible_pool
            target = (
                sorted(pool, key=lambda item: (-(item["rp5h"] or 0.0), item["model_id"]))[0]["model_id"]
                if pool
                else None
            )
            confidence = "baseline"
        else:
            target, _details = compute_mapping_for_request_model(score, candidates)
            target_match = next((c["match_type"] for c in candidates if c["model_id"] == target), "")
            confidence = _confidence(target_match)
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

    # Plan: channel lists + model card targets.
    provider_channels = decisions["channels"]
    channels: dict[str, dict[str, Any]] = {}
    for provider in sorted(owned):
        channel = provider_channels[provider]
        channels.setdefault(channel, {"supportedModels": []})["supportedModels"] = sorted(owned[provider])
    plan_models: list[dict[str, Any]] = []
    for channel in sorted(owned):
        axon_channel = provider_channels[channel]
        for model_id in sorted(owned[channel]):
            record = owned[channel][model_id]
            card = cards.get(model_id)
            if card is None:
                warnings.append({"type": "card_missing", "model": model_id, "provider": channel})
            merged = dict(card or {})
            merged["cost"] = _merge_channel_cost(card or {}, record)
            developer, icon, group = _model_meta(merged, axon_channel)
            remark = {"manual": ""}
            for field in REMARK_FIELDS:
                remark[field] = record.get(field)
            missing = [field for field in REMARK_FIELDS if remark[field] is None]
            if missing:
                warnings.append({"type": "missing_remark_fields", "model": model_id, "fields": missing})
            plan_models.append(
                {
                    "modelID": model_id,
                    "channel": axon_channel,
                    "input": {
                        "modelID": model_id,
                        "name": str(merged.get("name") or record.get("name") or model_id),
                        "developer": developer,
                        "type": "chat",
                        "icon": icon,
                        "group": group,
                        "modelCard": model_card(merged),
                        "remark": remark_json(remark),
                    },
                }
            )

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
