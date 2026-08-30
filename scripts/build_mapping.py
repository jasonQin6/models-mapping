#!/usr/bin/env python3
"""Build the deterministic model-mapping workspace.

The builder is the only process that writes ``models.csv``.  Its inputs are
three independent snapshots and a fixed request-model configuration:

* ``data/models.json`` -- the model IDs currently known by OpenCode;
* ``data/go.json`` -- OpenCode Go limits and prices parsed from ``go.mdx``;
* ``data/arena.json`` -- the Arena leaderboard;
* ``config/request-models.json`` -- the configured Claude/GPT request IDs.

The source model set is the exact union of models.json and go.json.  Joining
those two sources never applies name fallbacks.  Arena joins retain the
historical fallback chain from :mod:`name_matching` and record warnings for
low-confidence matches.  Missing source details are reported but do not
remove a model from the generated workspace; incomplete candidates simply do
not participate in target selection.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from csv_io import MAPPING_COLUMNS, write_mapping
from name_matching import find_best_match, normalize_arena_name


SCHEMA_VERSION = 1
DEFAULT_MODELS_PATH = Path("data/models.json")
DEFAULT_GO_PATH = Path("data/go.json")
DEFAULT_ARENA_PATH = Path("data/arena.json")
DEFAULT_REQUEST_MODELS_PATH = Path("config/request-models.json")
DEFAULT_MODEL_DECISIONS_PATH = Path("config/model-decisions.json")
DEFAULT_OUTPUT_PATH = Path("models.csv")

DEFAULT_WEIGHTS = {
    "score": 0.35,
    "rp5h": 0.30,
    "proximity": 0.35,
    "penalty_k": 0.2,
    "upgrade_bonus": 0.1,
}


class BuildError(ValueError):
    """Raised when a source cannot be loaded at all."""


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == "-"


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if _missing(value):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _csv_value(value: Any) -> str:
    """Render a nullable value for the five-column CSV contract."""

    if _missing(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _report_item(code: str, message: str, **details: Any) -> dict:
    item = {"code": code, "message": message}
    item.update(details)
    return item


def _validate_envelope(
    payload: Any,
    source_name: str,
    report: dict,
) -> None:
    """Require the agreed schema-versioned JSON envelope."""

    if not isinstance(payload, dict):
        report["errors"].append(
            _report_item("invalid_json_shape", f"{source_name} must be an object")
        )
        return
    if payload.get("schema_version") != SCHEMA_VERSION:
        report["errors"].append(
            _report_item(
                "unsupported_schema_version",
                f"{source_name} schema_version must be {SCHEMA_VERSION}",
                actual=payload.get("schema_version"),
            )
        )


def _iter_models_node(
    node: Any,
    provider: str,
) -> Iterable[Tuple[str, dict]]:
    """Yield exact model IDs and records from one provider node."""

    if isinstance(node, Mapping):
        models = node.get("models")
        if isinstance(models, Mapping):
            for key, value in models.items():
                if not isinstance(value, Mapping):
                    value = {"value": value}
                # For a mapping, the key is the canonical ID.  Do not apply
                # Arena-style normalization here: models.json ↔ go.json is an
                # exact join by contract.
                model_id = str(key).strip()
                if not model_id:
                    model_id = str(
                        value.get("id")
                        or value.get("model_id")
                        or value.get("modelID")
                        or ""
                    ).strip()
                if model_id:
                    yield model_id, {**dict(value), "provider": provider}
            return
        if isinstance(models, list):
            for value in models:
                if not isinstance(value, Mapping):
                    continue
                model_id = str(
                    value.get("id")
                    or value.get("model_id")
                    or value.get("modelID")
                    or ""
                ).strip()
                if model_id:
                    yield model_id, {**dict(value), "provider": provider}
            return

        # A single model record is accepted for callers constructing a small
        # fixture, although normal cache data is provider -> models.
        model_id = str(
            node.get("id") or node.get("model_id") or node.get("modelID") or ""
        ).strip()
        if model_id:
            yield model_id, {**dict(node), "provider": provider}


def load_models_data(payload: Any, report: Optional[dict] = None) -> Dict[str, dict]:
    """Load model IDs from normalized or raw OpenCode cache shapes."""

    target_report = report or {"errors": [], "warnings": []}
    models: Dict[str, dict] = {}

    if isinstance(payload, list):
        nodes: Iterable[Tuple[str, Any]] = [("", payload)]
    elif isinstance(payload, Mapping) and isinstance(payload.get("providers"), Mapping):
        nodes = payload["providers"].items()
    elif isinstance(payload, Mapping) and isinstance(payload.get("models"), (Mapping, list)):
        provider = str(payload.get("provider") or payload.get("providerID") or "unknown")
        nodes = [(provider, payload)]
    else:
        # Raw cache files historically used provider IDs as top-level keys.
        nodes = [
            (str(provider), node)
            for provider, node in payload.items()
        ] if isinstance(payload, Mapping) else []

    for provider, node in nodes:
        if provider == "" and isinstance(node, list):
            iterable = (
                (str(value.get("providerID") or value.get("provider") or "unknown"), value)
                for value in node
                if isinstance(value, Mapping)
            )
        else:
            iterable = _iter_models_node(node, str(provider))
        for model_id, value in iterable:
            if model_id in models:
                target_report["warnings"].append(
                    _report_item(
                        "duplicate_model_id",
                        f"model {model_id!r} appears more than once in models.json",
                        model_id=model_id,
                    )
                )
                # Keep the first provider deterministically; model IDs are
                # the identity used by the downstream AxonHub catalog.
                continue
            models[model_id] = value

    if not models:
        target_report["errors"].append(
            _report_item("empty_models_source", "models.json contains no models")
        )
    return models


def load_go_data(payload: Any, report: Optional[dict] = None) -> Dict[str, dict]:
    """Load go.json's exact model-ID keyed records."""

    target_report = report or {"errors": [], "warnings": []}
    if not isinstance(payload, Mapping):
        target_report["errors"].append(
            _report_item("invalid_go_source", "go.json must be an object")
        )
        return {}
    source_models = payload.get("models", payload)
    models: Dict[str, dict] = {}
    if isinstance(source_models, Mapping):
        for key, value in source_models.items():
            if not isinstance(value, Mapping):
                target_report["errors"].append(
                    _report_item("invalid_go_model", f"go model {key!r} is not an object")
                )
                continue
            model_id = str(key).strip()
            if not model_id:
                model_id = str(value.get("model_id") or value.get("id") or "").strip()
            if model_id:
                models[model_id] = dict(value)
    elif isinstance(source_models, list):
        for value in source_models:
            if not isinstance(value, Mapping):
                continue
            model_id = str(value.get("model_id") or value.get("id") or "").strip()
            if model_id:
                models[model_id] = dict(value)
    else:
        target_report["errors"].append(
            _report_item("invalid_go_models", "go.json models must be an object or array")
        )
    if not models:
        target_report["errors"].append(
            _report_item("empty_go_source", "go.json contains no models")
        )
    return models


def load_arena_data(payload: Any, report: Optional[dict] = None) -> Dict[str, dict]:
    """Load Arena records into the lookup expected by ``find_best_match``."""

    target_report = report or {"errors": [], "warnings": []}
    if isinstance(payload, Mapping):
        source_models = payload.get("models", payload)
    else:
        source_models = payload

    records: Iterable[Tuple[str, Any]]
    if isinstance(source_models, Mapping):
        records = source_models.items()
    elif isinstance(source_models, list):
        records = (
            (str(value.get("model_id") or value.get("id") or ""), value)
            for value in source_models
            if isinstance(value, Mapping)
        )
    else:
        target_report["errors"].append(
            _report_item("invalid_arena_models", "arena.json models must be an object or array")
        )
        return {}

    lookup: Dict[str, dict] = {}
    for raw_id, raw_value in records:
        if not isinstance(raw_value, Mapping):
            continue
        model_id = str(raw_id).strip()
        if not model_id:
            continue
        normalized, _ = normalize_arena_name(model_id)
        if not normalized:
            continue
        raw_rating = raw_value.get("arena_score", raw_value.get("rating"))
        rating = _number(raw_rating)
        # A row without a score cannot satisfy a request-model Arena join.
        if rating is None:
            target_report["errors"].append(
                _report_item(
                    "invalid_arena_score",
                    f"Arena model {model_id!r} has an invalid score",
                    model_id=model_id,
                    value=raw_rating,
                )
            )
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

    if not lookup:
        target_report["errors"].append(
            _report_item("empty_arena_source", "arena.json contains no scored models")
        )
    return lookup


def load_request_models_data(payload: Any, report: Optional[dict] = None) -> List[dict]:
    """Load the fixed request-model configuration."""

    target_report = report or {"errors": [], "warnings": []}
    if isinstance(payload, Mapping):
        source_models = payload.get("models", payload.get("request_models"))
        if source_models is None:
            # Permit an ID -> metadata mapping as a compact fixture.
            source_models = payload
    else:
        source_models = payload

    values: List[Any] = []
    if isinstance(source_models, Mapping):
        for key, value in source_models.items():
            if isinstance(value, Mapping):
                values.append({"model_id": key, **dict(value)})
            else:
                values.append({"model_id": key, "enabled": bool(value)})
    elif isinstance(source_models, list):
        values = list(source_models)
    else:
        target_report["errors"].append(
            _report_item(
                "invalid_request_models",
                "request-models.json must contain a models array or object",
            )
        )
        return []

    result: List[dict] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            record = {"model_id": value}
        elif isinstance(value, Mapping):
            record = dict(value)
        else:
            target_report["errors"].append(
                _report_item("invalid_request_model", "request model entry is not an object")
            )
            continue
        model_id = str(
            record.get("model_id") or record.get("id") or record.get("modelID") or ""
        ).strip()
        if not model_id:
            target_report["errors"].append(
                _report_item("request_model_without_id", "request model has no model_id")
            )
            continue
        if "enabled" in record and not isinstance(record["enabled"], bool):
            target_report["errors"].append(
                _report_item(
                    "invalid_request_enabled",
                    f"request model {model_id!r} enabled must be boolean",
                    model_id=model_id,
                )
            )
            continue
        if not record.get("enabled", True):
            continue
        if model_id in seen:
            target_report["errors"].append(
                _report_item("duplicate_request_model", f"request model {model_id!r} is duplicated")
            )
            continue
        seen.add(model_id)
        record["model_id"] = model_id
        result.append(record)

    if not result:
        target_report["warnings"].append(
            _report_item("empty_request_models", "no enabled request models configured")
        )
    return result


def load_model_decisions_data(
    payload: Any,
    report: Optional[dict] = None,
    *,
    provider: str = "opencode-go",
) -> dict[str, Any]:
    """Load reviewed exclusions, supplements, scope, and mapping overrides."""

    target_report = report or {"errors": [], "warnings": []}
    result = {
        "excluded": {},
        "supplements": {},
        "mapping_overrides": {},
        "scope": {},
    }
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
        target_report["errors"].append(
            _report_item(
                "invalid_model_decisions",
                "model-decisions.json must use schema_version 1",
            )
        )
        return result
    scope = payload.get("scope")
    if not isinstance(scope, Mapping):
        target_report["errors"].append(
            _report_item("invalid_decision_scope", "model decision scope must be an object")
        )
    else:
        channels = scope.get("channels")
        templates = scope.get("templates")
        if not isinstance(channels, list) or not all(isinstance(x, str) for x in channels):
            target_report["errors"].append(
                _report_item("invalid_decision_channels", "managed channels must be strings")
            )
        if not isinstance(templates, list) or not all(isinstance(x, str) for x in templates):
            target_report["errors"].append(
                _report_item("invalid_decision_templates", "managed templates must be strings")
            )
        result["scope"] = dict(scope)
    entries = payload.get("models")
    if not isinstance(entries, list):
        target_report["errors"].append(
            _report_item("invalid_model_decision_entries", "model decisions must be a list")
        )
        entries = []
    seen = set()
    allowed_supplements = {
        "rp5h",
        "usage_quota",
        "context_threshold",
        "peak_hours",
        "retention",
    }
    for entry in entries:
        if not isinstance(entry, Mapping):
            target_report["errors"].append(
                _report_item("invalid_model_decision", "model decision is not an object")
            )
            continue
        if str(entry.get("provider") or "") != provider:
            continue
        model_id = str(entry.get("model_id") or "").strip()
        action = str(entry.get("action") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        if not model_id or not reason or model_id in seen:
            target_report["errors"].append(
                _report_item("invalid_model_decision", "decision requires unique model_id and reason")
            )
            continue
        seen.add(model_id)
        if action == "exclude":
            result["excluded"][model_id] = reason
        elif action == "supplement":
            fields = entry.get("fields")
            if not isinstance(fields, Mapping) or not fields or set(fields) - allowed_supplements:
                target_report["errors"].append(
                    _report_item("invalid_model_supplement", f"invalid supplement for {model_id}")
                )
                continue
            result["supplements"][model_id] = dict(fields)
        else:
            target_report["errors"].append(
                _report_item("invalid_model_action", f"unsupported action for {model_id}")
            )
    overrides = payload.get("mapping_overrides", [])
    if not isinstance(overrides, list):
        target_report["errors"].append(
            _report_item("invalid_mapping_overrides", "mapping_overrides must be a list")
        )
        overrides = []
    for item in overrides:
        if not isinstance(item, Mapping):
            target_report["errors"].append(
                _report_item("invalid_mapping_override", "mapping override is not an object")
            )
            continue
        request_id = str(item.get("request_model") or "").strip()
        target_id = str(item.get("target_model") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not request_id or not target_id or not reason or request_id in result["mapping_overrides"]:
            target_report["errors"].append(
                _report_item("invalid_mapping_override", "override requires unique request, target, reason")
            )
            continue
        result["mapping_overrides"][request_id] = target_id
    return result


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


def group_by_series(request_models: Sequence[Mapping[str, Any]]) -> Dict[str, List[dict]]:
    """Group configured request records by Claude/GPT family."""

    groups: Dict[str, List[dict]] = {}
    for model in request_models:
        series = extract_series(str(model.get("model_id") or ""))
        if series:
            groups.setdefault(series, []).append(dict(model))
    return groups


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
    request_model_id: str,
    request_score: float,
    candidates: Sequence[Mapping[str, Any]],
    weights: Optional[Mapping[str, float]] = None,
    *,
    return_details: bool = False,
) -> Optional[str] | Tuple[Optional[str], Optional[dict]]:
    """Choose the highest-scoring eligible candidate for one request model."""

    eligible = []
    for candidate in candidates:
        model_id = str(candidate.get("model_id") or "").strip()
        score = _number(candidate.get("arena_score"))
        rp5h = _number(candidate.get("rp5h"))
        if model_id and score is not None and rp5h is not None:
            eligible.append(candidate)
    if not eligible:
        result: Tuple[Optional[str], Optional[dict]] = (None, None)
        return result if return_details else result[0]

    scores = [_number(item.get("arena_score"), 0.0) or 0.0 for item in eligible]
    max_values = {
        "max_score": max(scores) or 1.0,
        "max_rp5h": max(_number(item.get("rp5h"), 0.0) or 0.0 for item in eligible) or 1.0,
        "max_score_diff": (max(scores) - min(scores)) if len(scores) > 1 else 0.0,
    }
    ranked = sorted(
        (
            score_match(request_score, item, max_values, weights),
            str(item["model_id"]),
            item,
        )
        for item in eligible
    )
    # Python's tuple sort uses model ID as a tie-breaker ascending; negate the
    # first sort key explicitly and then use ID ascending for deterministic
    # results independent of source dictionary order.
    best_score, best_id, best_item = min(
        ranked,
        key=lambda item: (-item[0], item[1]),
    )
    details = {
        "target": best_id,
        "score": best_score,
        "candidate": best_item,
        "max_values": max_values,
    }
    result = (best_id, details)
    return result if return_details else result[0]


def _fix_free_records(records: Dict[str, dict]) -> Dict[str, dict]:
    """Preserve the source fix_free rule for the mapping input."""

    max_rp5h = max(
        (_number(value.get("rp5h"), 0.0) or 0.0)
        for model_id, value in records.items()
        if "free" not in model_id.lower()
    ) if any("free" not in model_id.lower() for model_id in records) else 0.0
    max_usage = max(
        (_number(value.get("usage_quota"), 0.0) or 0.0)
        for model_id, value in records.items()
        if "free" not in model_id.lower()
    ) if any("free" not in model_id.lower() for model_id in records) else 0.0
    for model_id, value in records.items():
        if "free" not in model_id.lower():
            continue
        if _missing(value.get("rp5h")):
            value["rp5h"] = int(max_rp5h) if max_rp5h.is_integer() else max_rp5h
        if _missing(value.get("usage_quota")):
            value["usage_quota"] = int(max_usage) if max_usage.is_integer() else max_usage
        for field in (
            "price_input",
            "price_output",
            "max_price_output",
            "price_cached_read",
            "price_cached_write",
        ):
            value[field] = 0
    return records


def _confidence(match_type: str) -> str:
    if match_type == "direct_match":
        return "high"
    if match_type in ("contributor_suffix", "version_downgrade"):
        return "medium"
    if match_type in ("prefix_match", "free_default"):
        return "low"
    return "none"


def _match_record(
    model_id: str,
    arena_lookup: Mapping[str, dict],
    *,
    is_free: bool = False,
) -> Tuple[Optional[dict], str]:
    """Call the shared fallback matcher while keeping a stable API here."""

    return find_best_match(model_id, dict(arena_lookup), is_free=is_free)


def build_mapping_data(
    models_payload: Any,
    go_payload: Any,
    arena_payload: Any,
    request_models_payload: Any,
    model_decisions_payload: Any | None = None,
) -> Tuple[List[dict], dict]:
    """Build CSV rows and a structured report from four in-memory sources."""

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "errors": [],
        "warnings": [],
        "counts": {},
        "mappings": [],
        "excluded": [],
        "decision_required": [],
    }
    _validate_envelope(models_payload, "models.json", report)
    _validate_envelope(go_payload, "go.json", report)
    _validate_envelope(arena_payload, "arena.json", report)
    _validate_envelope(request_models_payload, "request-models.json", report)
    if model_decisions_payload is None:
        model_decisions_payload = {
            "schema_version": 1,
            "scope": {"channels": [], "templates": []},
            "models": [],
            "mapping_overrides": [],
        }
    _validate_envelope(model_decisions_payload, "model-decisions.json", report)

    models = load_models_data(models_payload, report)
    go_models = load_go_data(go_payload, report)
    arena_lookup = load_arena_data(arena_payload, report)
    requests = load_request_models_data(request_models_payload, report)
    decisions = load_model_decisions_data(model_decisions_payload, report)
    for model_id, fields in decisions["supplements"].items():
        go_models.setdefault(model_id, {}).update(fields)
    go_models = _fix_free_records({key: dict(value) for key, value in go_models.items()})

    request_ids = {str(item["model_id"]) for item in requests}
    cache_only_ids = (set(models) - set(go_models)) - request_ids
    go_only_ids = (set(go_models) - set(models)) - request_ids
    for model_id in sorted(cache_only_ids):
        report["excluded"].append(
            {"model_id": model_id, "reason": "absent_in_go"}
        )
    for model_id in sorted(go_only_ids):
        report["excluded"].append(
            {"model_id": model_id, "reason": "absent_in_models"}
        )
    intersection_ids = (set(models) & set(go_models)) - request_ids
    manual_excluded = set(decisions["excluded"])
    for model_id in sorted(intersection_ids & manual_excluded):
        report["excluded"].append(
            {
                "model_id": model_id,
                "reason": "manual_exclude",
                "detail": decisions["excluded"][model_id],
            }
        )
    candidate_ids = intersection_ids - manual_excluded
    max_source_rp5h = max(
        (_number(item.get("rp5h"), 0.0) or 0.0) for item in go_models.values()
    ) if go_models else 0.0
    candidate_rows: List[dict] = []
    candidate_meta: Dict[str, dict] = {}

    for model_id in sorted(candidate_ids):
        go_record = dict(go_models.get(model_id, {}))
        is_free = "free" in model_id.lower()
        if is_free and _number(go_record.get("rp5h")) is None:
            go_record["rp5h"] = max_source_rp5h
        arena_entry, match_type = _match_record(model_id, arena_lookup, is_free=is_free)
        arena_score: Any = ""
        if arena_entry is None or match_type == "no_match":
            if not is_free:
                report["warnings"].append(
                    _report_item(
                        "candidate_arena_missing",
                        f"no Arena match for candidate {model_id!r}",
                        model_id=model_id,
                    )
                )
        else:
            arena_score = arena_entry.get("rating", 0)
            if match_type in ("version_downgrade", "prefix_match"):
                report["warnings"].append(
                    _report_item(
                        "candidate_arena_fallback",
                        f"Arena {match_type} used for candidate {model_id!r}",
                        model_id=model_id,
                        match_type=match_type,
                        matched_to=next(
                            (
                                key
                                for key, value in arena_lookup.items()
                                if value is arena_entry
                            ),
                            None,
                        ),
                    )
                )

        missing_fields = []
        for field in ("rp5h",):
            if _number(go_record.get(field)) is None:
                missing_fields.append(field)
                if not is_free:
                    report["decision_required"].append(
                        _report_item(
                            "candidate_field_missing",
                            f"candidate {model_id!r} has no required {field} in go.json",
                            model_id=model_id,
                            field=field,
                        )
                    )

        eligible = (
            _number(go_record.get("rp5h")) is not None
            and arena_entry is not None
        )
        candidate_row = {
            "model_id": model_id,
            "role": "candidate",
            "arena_score": _csv_value(arena_score),
            "rp5h": _csv_value(go_record.get("rp5h")),
            "mapping": "",
        }
        candidate_rows.append(candidate_row)
        candidate_meta[model_id] = {
            "row": candidate_row,
            "record": {
                "model_id": model_id,
                "arena_score": arena_score,
                "rp5h": go_record.get("rp5h"),
            },
            "eligible": eligible,
            "match_type": match_type,
            "missing_fields": missing_fields,
        }

    eligible_candidates = [
        meta["record"]
        for meta in candidate_meta.values()
        if meta["eligible"]
    ]
    candidate_by_id = {item["model_id"]: item for item in eligible_candidates}
    request_rows: List[dict] = []
    request_meta: List[dict] = []
    for request in requests:
        model_id = request["model_id"]
        arena_id = str(
            request.get("arena_model_id")
            or request.get("arena_id")
            or request.get("arena")
            or model_id
        ).strip()
        arena_entry, match_type = _match_record(arena_id, arena_lookup)
        score = None
        if arena_entry is None or match_type == "no_match":
            report["errors"].append(
                _report_item(
                    "request_arena_missing",
                    f"no Arena score for request model {model_id!r}",
                    model_id=model_id,
                    arena_id=arena_id,
                )
            )
        else:
            score = _number(arena_entry.get("rating"))
            if match_type in ("version_downgrade", "prefix_match"):
                report["warnings"].append(
                    _report_item(
                        "request_arena_fallback",
                        f"Arena {match_type} used for request {model_id!r}",
                        model_id=model_id,
                        match_type=match_type,
                    )
                )

        request_meta.append(
            {
                "config": request,
                "model_id": model_id,
                "arena_score": score,
                "match_type": match_type,
                "current_target": request.get("current_target") or request.get("mapping"),
            }
        )

    # Baselines are computed only from requests with a valid Arena score.
    baselines: Dict[str, str] = {}
    grouped = group_by_series(
        [
            {"model_id": item["model_id"], "arena_score": item["arena_score"]}
            for item in request_meta
            if item["arena_score"] is not None
        ]
    )
    for series, group in grouped.items():
        baseline = find_baseline_model(group)
        if baseline:
            baselines[series] = baseline

    free_candidates = [
        item for item in eligible_candidates if "free" in item["model_id"].lower()
    ]
    free_candidates.sort(key=lambda item: (-(_number(item.get("rp5h"), 0.0) or 0.0), item["model_id"]))
    all_by_rp5h = sorted(
        eligible_candidates,
        key=lambda item: (-(_number(item.get("rp5h"), 0.0) or 0.0), item["model_id"]),
    )

    for item in request_meta:
        model_id = item["model_id"]
        target: Optional[str] = None
        details: Optional[dict] = None
        score = item["arena_score"]
        if score is not None:
            series = extract_series(model_id)
            if baselines.get(series) == model_id:
                baseline_pool = free_candidates or all_by_rp5h
                if baseline_pool:
                    target = baseline_pool[0]["model_id"]
                    details = {"score": None, "baseline": True, "candidate": baseline_pool[0]}
                else:
                    report["errors"].append(
                        _report_item(
                            "request_without_target",
                            f"no eligible candidate for baseline request {model_id!r}",
                            model_id=model_id,
                        )
                    )
            else:
                target, details = compute_mapping_for_request_model(
                    model_id,
                    score,
                    eligible_candidates,
                    return_details=True,
                )
                if target is None:
                    report["errors"].append(
                        _report_item(
                            "request_without_target",
                            f"no eligible candidate for request {model_id!r}",
                            model_id=model_id,
                        )
                    )

        target_meta = candidate_meta.get(target or "")
        override = decisions["mapping_overrides"].get(model_id)
        if override:
            if override not in candidate_by_id:
                report["errors"].append(
                    _report_item(
                        "invalid_mapping_override_target",
                        f"mapping override target {override!r} is not eligible",
                        model_id=model_id,
                    )
                )
                target = None
                details = None
            else:
                target = override
                details = {
                    "score": None,
                    "override": True,
                    "candidate": candidate_by_id[override],
                }
            target_meta = candidate_meta.get(target or "")
        request_row = {
            "model_id": model_id,
            "role": "request",
            "arena_score": _csv_value(score),
            "rp5h": "",
            "mapping": target or "",
        }
        request_rows.append(request_row)
        report["mappings"].append(
            {
                "request_model": model_id,
                "current_target": item["current_target"],
                "suggested_target": target,
                "arena_score": score,
                "rp5h": (
                    target_meta["record"].get("rp5h")
                    if target_meta
                    else None
                ),
                "match_confidence": (
                    "baseline"
                    if details and details.get("baseline")
                    else _confidence(target_meta["match_type"])
                    if target_meta
                    else "none"
                ),
            }
        )

    candidate_rows.sort(
        key=lambda row: (
            -(
                _number(row.get("arena_score"))
                if _number(row.get("arena_score")) is not None
                else -1.0
            ),
            row["model_id"],
        )
    )
    rows = candidate_rows + request_rows
    report["counts"] = {
        "candidate_total": len(candidate_rows),
        "candidate_eligible": len(eligible_candidates),
        "request_total": len(request_rows),
        "mapped": sum(1 for row in request_rows if row["mapping"]),
        "errors": len(report["errors"]),
        "warnings": len(report["warnings"]),
        "decision_required": len(report["decision_required"]),
        "excluded": len(report["excluded"]),
    }
    return rows, report


def _read_json(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise BuildError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BuildError(f"invalid {label} JSON ({path}): {exc}") from exc


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def render_report(report: Mapping[str, Any]) -> str:
    """Render the compact human review table shown by CI and local runs."""

    lines = [
        "build-mapping report",
        json.dumps(report.get("counts", {}), ensure_ascii=False, sort_keys=True),
    ]
    for item in report.get("errors", []):
        lines.append(f"ERROR [{item.get('code')}] {item.get('message')}")
    for item in report.get("warnings", []):
        lines.append(f"WARNING [{item.get('code')}] {item.get('message')}")
    for item in report.get("decision_required", []):
        lines.append(
            f"DECISION REQUIRED [{item.get('code')}] {item.get('message')}"
        )
    for item in report.get("excluded", []):
        lines.append(
            f"EXCLUDED [{item.get('reason')}] {item.get('model_id')}"
        )
    lines.append("")
    lines.append("Request model | current target | suggested target | Arena score | RP5H | confidence")
    for item in report.get("mappings", []):
        lines.append(
            " | ".join(
                str(item.get(field) if item.get(field) is not None else "")
                for field in (
                    "request_model",
                    "current_target",
                    "suggested_target",
                    "arena_score",
                    "rp5h",
                    "match_confidence",
                )
            )
        )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build models.csv from three source snapshots")
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS_PATH)
    parser.add_argument("--go", type=Path, default=DEFAULT_GO_PATH)
    parser.add_argument("--arena", type=Path, default=DEFAULT_ARENA_PATH)
    parser.add_argument("--request-models", type=Path, default=DEFAULT_REQUEST_MODELS_PATH)
    parser.add_argument(
        "--model-decisions",
        type=Path,
        default=DEFAULT_MODEL_DECISIONS_PATH,
    )
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report-output", type=Path, help="Optional JSON report path")
    parser.add_argument("--json-report", action="store_true", help="Print the full report as JSON")
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero after writing CSV when blocking data errors are present",
    )
    args = parser.parse_args(argv)

    try:
        models_payload = _read_json(args.models, "models.json")
        go_payload = _read_json(args.go, "go.json")
        arena_payload = _read_json(args.arena, "arena.json")
        request_payload = _read_json(args.request_models, "request-models.json")
        decisions_payload = _read_json(args.model_decisions, "model-decisions.json")
        rows, report = build_mapping_data(
            models_payload,
            go_payload,
            arena_payload,
            request_payload,
            decisions_payload,
        )
        write_mapping(args.output, rows)
        if args.report_output:
            _write_json_atomic(args.report_output, report)
    except (BuildError, OSError, ValueError) as exc:
        print(f"build-mapping: {exc}", file=sys.stderr)
        return 1

    if args.json_report:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    print(f"build-mapping: wrote {len(rows)} rows to {args.output}")
    if args.fail_on_errors and (report["errors"] or report["decision_required"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
