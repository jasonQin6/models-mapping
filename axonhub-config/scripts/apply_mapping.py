#!/usr/bin/env python3
"""Preview or apply confirmed request-model associations in AxonHub.

The mapping workflow is deliberately narrow: ``models.csv`` supplies a fixed
set of request-model -> candidate-model pairs, and each request model is
reconciled to exactly one enabled ``type=model`` association.  This helper
never creates request models, edits candidate models, creates profile
templates, or touches channel associations on unrelated models.
"""

from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import fetch_connection, fetch_graphql


CSV_COLUMNS = ("model_id", "role", "arena_score", "rp5h", "mapping")
ASSOCIATION_KEYS = (
    "type",
    "priority",
    "disabled",
    "channelModel",
    "channelRegex",
    "regex",
    "modelId",
    "channelTagsModel",
    "channelTagsRegex",
)
MODEL_QUERY = """
query ListModels($first: Int!, $after: Cursor) {
  models(first: $first, after: $after) {
    edges {
      node {
        id
        modelID
        status
        settings {
          disableDeveloperSettingsInheritance
          associations {
            type
            priority
            disabled
            channelModel { channelId modelId }
            modelId { modelId }
          }
          loadBalancerStrategy
          traceStickyMode
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""
UPDATE_MODEL_MUTATION = """
mutation UpdateModel($id: ID!, $input: UpdateModelInput!) {
  updateModel(id: $id, input: $input) {
    id
    modelID
  }
}
"""
CREATE_TEMPLATE_MUTATION = """
mutation CreateTemplate($input: CreateAPIKeyProfileTemplateInput!, $profile: APIKeyProfileInput!) {
  createApiKeyProfileTemplate(input: $input, profile: $profile) { id name }
}
"""
UPDATE_TEMPLATE_MUTATION = """
mutation UpdateTemplate($id: ID!, $input: UpdateAPIKeyProfileTemplateInput!, $profile: APIKeyProfileInput) {
  updateApiKeyProfileTemplate(id: $id, input: $input, profile: $profile) { id name }
}
"""


class MappingInputError(ValueError):
    """Raised when the generated mapping artifact cannot be applied."""


@dataclass(frozen=True)
class MappingRow:
    """One row from the generated mapping workspace."""

    model_id: str
    role: str
    arena_score: str
    rp5h: str
    mapping: str


def read_mapping_rows(path: Path) -> list[MappingRow]:
    """Read and validate the generated five-column mapping workspace."""

    if not path.exists():
        raise MappingInputError(f"mapping file not found: {path}")

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if fields != CSV_COLUMNS:
            raise MappingInputError(
                f"mapping file columns must be exactly {', '.join(CSV_COLUMNS)}"
            )

        rows: list[MappingRow] = []
        seen: set[str] = set()
        errors: list[str] = []
        for line_number, raw in enumerate(reader, start=2):
            model_id = (raw.get("model_id") or "").strip()
            role = (raw.get("role") or "").strip().lower()
            mapping = (raw.get("mapping") or "").strip()
            if not model_id:
                errors.append(f"line {line_number}: model_id is empty")
                continue
            if model_id in seen:
                errors.append(f"line {line_number}: duplicate model_id {model_id!r}")
                continue
            seen.add(model_id)
            if role not in {"candidate", "request"}:
                errors.append(
                    f"line {line_number}: unsupported role {role!r} for {model_id!r}"
                )
            if role == "request" and not mapping:
                errors.append(f"line {line_number}: request {model_id!r} has empty mapping")
            arena_score = (raw.get("arena_score") or "").strip()
            if role == "request":
                try:
                    numeric_score = float(arena_score)
                except ValueError:
                    numeric_score = math.nan
                if not math.isfinite(numeric_score):
                    errors.append(
                        f"line {line_number}: request {model_id!r} has invalid arena_score"
                    )
            rows.append(
                MappingRow(
                    model_id=model_id,
                    role=role,
                    arena_score=arena_score,
                    rp5h=(raw.get("rp5h") or "").strip(),
                    mapping=mapping,
                )
            )

    candidates = {row.model_id for row in rows if row.role == "candidate"}
    for row in rows:
        if row.role == "request" and row.mapping and row.mapping not in candidates:
            errors.append(
                f"request {row.model_id!r} maps to unknown candidate {row.mapping!r}"
            )
    if errors:
        raise MappingInputError("; ".join(errors))
    if not any(row.role == "request" for row in rows):
        raise MappingInputError("mapping file contains no request rows")
    if not candidates:
        raise MappingInputError("mapping file contains no candidate rows")
    return rows


def parse_mapping_csv(path: Path) -> dict[str, str]:
    """Return request -> candidate pairs from a validated mapping workspace."""

    return {
        row.model_id: row.mapping
        for row in read_mapping_rows(path)
        if row.role == "request"
    }


def load_fixed_request_ids(path: Path) -> set[str]:
    """Load the exact enabled request-model boundary."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MappingInputError(f"invalid request-model config: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise MappingInputError("request-model config must use schema_version 1")
    models = payload.get("models")
    if not isinstance(models, list):
        raise MappingInputError("request-model config models must be a list")
    result: set[str] = set()
    for item in models:
        if not isinstance(item, dict) or not isinstance(item.get("enabled", True), bool):
            raise MappingInputError("request-model config contains an invalid entry")
        if not item.get("enabled", True):
            continue
        model_id = str(item.get("model_id") or "").strip()
        if not model_id or model_id in result:
            raise MappingInputError("request-model config contains a missing or duplicate ID")
        result.add(model_id)
    if not result:
        raise MappingInputError("request-model config has no enabled models")
    return result


def load_managed_template_names(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MappingInputError(f"invalid model-decisions config: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise MappingInputError("model-decisions config must use schema_version 1")
    scope = payload.get("scope")
    templates = scope.get("templates") if isinstance(scope, dict) else None
    if not isinstance(templates, list) or not all(isinstance(x, str) for x in templates):
        raise MappingInputError("model-decisions managed templates are invalid")
    return set(templates)


def build_model_association(target_model: str) -> dict[str, Any]:
    """Build the canonical one-to-one AxonHub model association."""

    if not target_model.strip():
        raise ValueError("target_model must not be empty")
    return {
        "type": "model",
        "priority": 0,
        "disabled": False,
        "channelModel": None,
        "channelRegex": None,
        "regex": None,
        "modelId": {"modelId": target_model, "exclude": None},
        "channelTagsModel": None,
        "channelTagsRegex": None,
    }


def normalize_association(association: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize omitted GraphQL nullable fields before a semantic diff."""

    association = association or {}
    normalized = {key: copy.deepcopy(association.get(key)) for key in ASSOCIATION_KEYS}
    model_id = normalized.get("modelId")
    if isinstance(model_id, str):
        normalized["modelId"] = {"modelId": model_id, "exclude": None}
    elif isinstance(model_id, dict):
        normalized["modelId"] = {
            "modelId": model_id.get("modelId"),
            "exclude": model_id.get("exclude"),
        }
    return normalized


def settings_with_single_model_association(
    settings: dict[str, Any] | None, target_model: str
) -> dict[str, Any]:
    """Preserve every non-association setting while replacing associations."""

    updated = copy.deepcopy(settings or {})
    updated["associations"] = [build_model_association(target_model)]
    return updated


def settings_fingerprint(settings: dict[str, Any] | None) -> str:
    """Return a stable, secret-free fingerprint for optimistic concurrency."""

    payload = json.dumps(
        settings or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def associations_match(
    settings: dict[str, Any] | None, target_model: str
) -> bool:
    """Return whether settings already have exactly the canonical association."""

    associations = (settings or {}).get("associations") or []
    if len(associations) != 1:
        return False
    return normalize_association(associations[0]) == normalize_association(
        build_model_association(target_model)
    )


def current_target(settings: dict[str, Any] | None) -> str | None:
    """Describe the currently effective association target, if one exists."""

    associations = (settings or {}).get("associations") or []
    enabled_models = []
    for association in associations:
        if association.get("type") != "model" or association.get("disabled", False):
            continue
        model_id = association.get("modelId")
        if isinstance(model_id, dict):
            model_id = model_id.get("modelId")
        if model_id:
            enabled_models.append(model_id)
    if enabled_models:
        return ", ".join(str(value) for value in enabled_models)

    legacy = [
        association.get("channelModel")
        for association in associations
        if association.get("type") == "channel_model"
        and not association.get("disabled", False)
        and isinstance(association.get("channelModel"), dict)
    ]
    if legacy:
        parts = []
        for value in legacy:
            parts.append(f"channel:{value.get('channelId')}/{value.get('modelId')}")
        return "; ".join(parts)
    return None


def _graphql_data(
    axonhub_url: str, token: str, query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call GraphQL and convert protocol errors into one clear exception."""

    result = fetch_graphql(axonhub_url, token, query, variables)
    if result.get("errors"):
        raise RuntimeError(json.dumps(result["errors"], ensure_ascii=False))
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("AxonHub GraphQL response did not contain data")
    return data


def fetch_all_models(
    axonhub_url: str, token: str, page_size: int = 100
) -> dict[str, dict[str, Any]]:
    """Fetch every model through the Relay connection, not just the first page."""

    if page_size < 1:
        raise ValueError("page_size must be positive")
    models: dict[str, dict[str, Any]] = {}
    after: str | None = None
    while True:
        data = _graphql_data(
            axonhub_url,
            token,
            MODEL_QUERY,
            {"first": page_size, "after": after},
        )
        connection = data.get("models") or {}
        edges = connection.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            model_id = node.get("modelID")
            internal_id = node.get("id")
            if model_id and internal_id:
                models[str(model_id)] = node

        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor == after:
            raise RuntimeError("AxonHub models pagination did not advance")
        after = str(next_cursor)
    return models


TEMPLATE_SELECTION = (
    "id name description projectID linkedProfilesCount "
    "profile { name modelMappings { from to } channelIDs channelTags "
    "channelTagsMatchMode modelIDs loadBalanceStrategy traceStickyMode "
    "quota { requests totalTokens cost period { type "
    "pastDuration { value unit } calendarDuration { unit } } } }"
)


def fetch_managed_templates(
    axonhub_url: str, token: str, managed_names: set[str]
) -> dict[str, dict[str, Any]]:
    nodes = fetch_connection(
        axonhub_url,
        token,
        "apiKeyProfileTemplates",
        TEMPLATE_SELECTION,
    )
    return {
        str(node["name"]): node
        for node in nodes
        if node.get("name") in managed_names
    }


def template_fingerprint(node: dict[str, Any] | None) -> str:
    if node is None:
        return settings_fingerprint(None)
    return settings_fingerprint(
        {
            "id": node.get("id"),
            "name": node.get("name"),
            "description": node.get("description"),
            "projectID": node.get("projectID"),
            "profile": node.get("profile"),
            "linkedProfilesCount": node.get("linkedProfilesCount"),
        }
    )


def mapping_plan_hash(plan: dict[str, Any]) -> str:
    payload = dict(plan)
    payload.pop("planHash", None)
    return settings_fingerprint(payload)


def profile_with_mappings(
    existing_profile: dict[str, Any] | None,
    template_name: str,
    mappings: dict[str, str],
) -> dict[str, Any]:
    profile = copy.deepcopy(existing_profile or {})
    profile["name"] = template_name
    profile["modelMappings"] = [
        {"from": source, "to": mappings[source]} for source in sorted(mappings)
    ]
    profile.setdefault("channelIDs", [])
    profile.setdefault("channelTags", [])
    profile.setdefault("channelTagsMatchMode", "any")
    profile.setdefault("modelIDs", [])
    profile.setdefault("loadBalanceStrategy", "default")
    profile.setdefault("traceStickyMode", "default")
    if profile.get("quota") is None:
        profile.pop("quota", None)
    profile.pop("templateID", None)
    profile.pop("templateName", None)
    return profile


def mappings_dict(profile: dict[str, Any] | None) -> dict[str, str]:
    result = {}
    for item in (profile or {}).get("modelMappings") or []:
        source = str(item.get("from") or "").strip()
        target = str(item.get("to") or "").strip()
        if source and target:
            result[source] = target
    return result


def normalize_profile_for_compare(
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    value = copy.deepcopy(profile or {})
    for field in ("channelIDs", "channelTags", "modelIDs"):
        value[field] = value.get(field) or []
    value["modelMappings"] = sorted(
        value.get("modelMappings") or [],
        key=lambda item: (item.get("from", ""), item.get("to", "")),
    )
    if value.get("quota") is None:
        value.pop("quota", None)
    return value


def build_template_plan(
    mapping: dict[str, str],
    fixed_request_ids: set[str],
    managed_template_names: set[str],
    existing: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    required_names = {"stable", "claude", "gpt"}
    if managed_template_names != required_names:
        raise MappingInputError(
            "managed templates must be exactly stable, claude, and gpt"
        )
    managed_claude = {
        source: target for source, target in mapping.items() if source.startswith("claude-")
    }
    managed_gpt = {
        source: target for source, target in mapping.items() if source.startswith("gpt-")
    }
    if set(managed_claude) | set(managed_gpt) != fixed_request_ids:
        raise MappingInputError("fixed request models must belong to claude or gpt")

    child_results: dict[str, dict[str, str]] = {}
    for name, managed in (("claude", managed_claude), ("gpt", managed_gpt)):
        current = mappings_dict((existing.get(name) or {}).get("profile"))
        manual = {
            source: target
            for source, target in current.items()
            if source not in fixed_request_ids
        }
        child_results[name] = {**manual, **managed}

    union = dict(child_results["claude"])
    for source, target in child_results["gpt"].items():
        if source in union and union[source] != target:
            raise MappingInputError(
                f"manual template mapping conflict for {source!r}"
            )
        union[source] = target

    stable_current = mappings_dict((existing.get("stable") or {}).get("profile"))
    stable_only = {
        source: target for source, target in stable_current.items() if source not in union
    }
    desired_mappings = {
        "claude": child_results["claude"],
        "gpt": child_results["gpt"],
        "stable": {**stable_only, **union},
    }
    changes = []
    creates = []
    noops = []
    warnings = []
    if stable_only:
        warnings.append(
            {
                "code": "stable_only_manual_mappings",
                "mappings": stable_only,
            }
        )
    project_id = next(
        (node.get("projectID") for node in existing.values() if node.get("projectID")),
        None,
    )
    for name in ("claude", "gpt", "stable"):
        node = existing.get(name)
        profile = profile_with_mappings(
            (node or {}).get("profile"), name, desired_mappings[name]
        )
        item = {
            "name": name,
            "profile": profile,
            "beforeFingerprint": template_fingerprint(node),
            "linkedProfilesCount": int((node or {}).get("linkedProfilesCount") or 0),
            "beforeMappings": mappings_dict((node or {}).get("profile")),
            "afterMappings": desired_mappings[name],
        }
        if node is None:
            if project_id is None:
                raise MappingInputError("cannot create gpt template without a project ID")
            item["projectID"] = project_id
            item["description"] = "Managed GPT model mappings"
            creates.append(item)
        elif normalize_profile_for_compare(node.get("profile")) == normalize_profile_for_compare(profile):
            item["internalID"] = node["id"]
            noops.append(item)
        else:
            item["internalID"] = node["id"]
            changes.append(item)
    return {
        "changes": changes,
        "creates": creates,
        "noops": noops,
        "warnings": warnings,
    }


def build_plan(
    mapping_path: Path,
    request_models_path: Path,
    model_decisions_path: Path,
    axonhub_url: str,
    token: str,
) -> dict[str, Any]:
    """Build a read-only plan and collect blocking/warning findings."""

    rows = read_mapping_rows(mapping_path)
    requests = [row for row in rows if row.role == "request"]
    fixed_request_ids = load_fixed_request_ids(request_models_path)
    managed_template_names = load_managed_template_names(model_decisions_path)
    csv_request_ids = {row.model_id for row in requests}
    if csv_request_ids != fixed_request_ids:
        missing = sorted(fixed_request_ids - csv_request_ids)
        extra = sorted(csv_request_ids - fixed_request_ids)
        raise MappingInputError(
            f"mapping requests differ from fixed config; missing={missing}, extra={extra}"
        )
    remote_models = fetch_all_models(axonhub_url, token)
    remote_templates = fetch_managed_templates(
        axonhub_url, token, managed_template_names
    )
    changes: list[dict[str, Any]] = []
    noops: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for row in requests:
        request_node = remote_models.get(row.model_id)
        target_node = remote_models.get(row.mapping)
        if request_node is None:
            errors.append(
                {
                    "code": "request_model_missing",
                    "model": row.model_id,
                    "message": "request model does not exist in AxonHub",
                }
            )
            continue
        if target_node is None:
            errors.append(
                {
                    "code": "target_model_missing",
                    "model": row.mapping,
                    "message": f"target for {row.model_id} does not exist in AxonHub",
                }
            )
            continue
        if str(request_node.get("status", "")).lower() != "enabled":
            errors.append(
                {
                    "code": "request_model_disabled",
                    "model": row.model_id,
                    "message": "request model is not enabled in AxonHub",
                }
            )
            continue
        if str(target_node.get("status", "")).lower() != "enabled":
            errors.append(
                {
                    "code": "target_model_disabled",
                    "model": row.mapping,
                    "message": f"target for {row.model_id} is not enabled in AxonHub",
                }
            )
            continue

        settings = request_node.get("settings") or {}
        associations = settings.get("associations") or []
        if any(association.get("type") == "channel_model" for association in associations):
            warnings.append(
                {
                    "code": "legacy_channel_association",
                    "model": row.model_id,
                    "message": "existing channel_model association will be replaced",
                }
            )
        if len(associations) != 1:
            warnings.append(
                {
                    "code": "association_shape",
                    "model": row.model_id,
                    "message": f"existing association count is {len(associations)}",
                }
            )

        item = {
            "requestModel": row.model_id,
            "targetModel": row.mapping,
            "internalID": request_node["id"],
            "currentTarget": current_target(settings),
            "currentAssociationCount": len(associations),
            "currentSettingsFingerprint": settings_fingerprint(settings),
            "settings": settings_with_single_model_association(settings, row.mapping),
        }
        if associations_match(settings, row.mapping):
            noops.append(item)
        else:
            changes.append(item)

    template_plan = build_template_plan(
        {row.model_id: row.mapping for row in requests},
        fixed_request_ids,
        managed_template_names,
        remote_templates,
    )
    return {
        "schemaVersion": 1,
        "mappingFile": str(mapping_path),
        "requestModelsFile": str(request_models_path),
        "modelDecisionsFile": str(model_decisions_path),
        "fixedRequestModels": sorted(fixed_request_ids),
        "managedTemplates": sorted(managed_template_names),
        "requestCount": len(requests),
        "changes": changes,
        "noops": noops,
        "errors": errors,
        "warnings": warnings,
        "templates": template_plan,
    }


def validate_saved_plan(
    plan: dict[str, Any],
    fixed_request_ids: set[str],
    current_models: dict[str, dict[str, Any]],
    managed_template_names: set[str],
    current_templates: dict[str, dict[str, Any]],
) -> None:
    """Validate a reviewed plan and bind it to current model identities/state."""

    if plan.get("schemaVersion") != 1:
        raise MappingInputError("unsupported mapping plan schemaVersion")
    if plan.get("planHash") != mapping_plan_hash(plan):
        raise MappingInputError("mapping plan hash is missing or invalid")
    for field in ("changes", "noops", "errors", "warnings"):
        if not isinstance(plan.get(field), list):
            raise MappingInputError(f"mapping plan field {field} must be a list")
    items = list(plan["changes"]) + list(plan["noops"])
    request_ids = {str(item.get("requestModel") or "") for item in items}
    if request_ids != fixed_request_ids or set(plan.get("fixedRequestModels") or []) != fixed_request_ids:
        raise MappingInputError("mapping plan does not match the fixed request-model set")
    if set(plan.get("managedTemplates") or []) != managed_template_names:
        raise MappingInputError("mapping plan does not match the managed template set")
    allowed_settings = {
        "disableDeveloperSettingsInheritance",
        "associations",
        "loadBalancerStrategy",
        "traceStickyMode",
    }
    drifted: list[str] = []
    for item in items:
        request_id = str(item.get("requestModel") or "")
        target_id = str(item.get("targetModel") or "")
        request_node = current_models.get(request_id)
        target_node = current_models.get(target_id)
        if request_node is None:
            drifted.append(f"{request_id}: request model is missing")
            continue
        if target_node is None:
            drifted.append(f"{request_id}: target {target_id} is missing")
            continue
        if str(request_node.get("id")) != str(item.get("internalID")):
            drifted.append(f"{request_id}: internal ID changed")
        if str(request_node.get("status", "")).lower() != "enabled":
            drifted.append(f"{request_id}: request model is disabled")
        if str(target_node.get("status", "")).lower() != "enabled":
            drifted.append(f"{request_id}: target {target_id} is disabled")
        settings = item.get("settings")
        if not isinstance(settings, dict) or set(settings) - allowed_settings:
            raise MappingInputError(f"{request_id}: plan contains invalid settings fields")
        expected_settings = settings_with_single_model_association(
            request_node.get("settings"), target_id
        )
        if settings != expected_settings:
            raise MappingInputError(f"{request_id}: plan settings were modified")
        expected_fingerprint = item.get("currentSettingsFingerprint")
        if settings_fingerprint(request_node.get("settings")) != expected_fingerprint:
            drifted.append(f"{request_id}: settings changed since plan")
    if drifted:
        raise MappingInputError(
            "plan is stale; regenerate dry-run before apply: " + "; ".join(drifted)
        )
    template_plan = plan.get("templates")
    if not isinstance(template_plan, dict):
        raise MappingInputError("mapping plan has no template section")
    for field in ("changes", "creates", "noops", "warnings"):
        if not isinstance(template_plan.get(field), list):
            raise MappingInputError(f"template plan field {field} must be a list")
    template_items = (
        list(template_plan["changes"])
        + list(template_plan["creates"])
        + list(template_plan["noops"])
    )
    if {str(item.get("name") or "") for item in template_items} != managed_template_names:
        raise MappingInputError("template plan does not cover the managed templates")
    for item in template_items:
        name = str(item.get("name") or "")
        current = current_templates.get(name)
        if item in template_plan["creates"]:
            if current is not None:
                drifted.append(f"template {name} now exists")
        else:
            if current is None:
                drifted.append(f"template {name} disappeared")
            elif str(current.get("id")) != str(item.get("internalID")):
                drifted.append(f"template {name} internal ID changed")
            elif template_fingerprint(current) != item.get("beforeFingerprint"):
                drifted.append(f"template {name} changed since plan")
        if not isinstance(item.get("profile"), dict):
            raise MappingInputError(f"template {name} has an invalid profile")
    if drifted:
        raise MappingInputError(
            "plan is stale; regenerate dry-run before apply: " + "; ".join(drifted)
        )


def apply_plan(
    axonhub_url: str,
    token: str,
    plan: dict[str, Any],
    current_models: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply planned changes and continue collecting per-model failures."""

    if plan.get("errors"):
        raise MappingInputError("cannot apply a plan containing blocking errors")

    if current_models is not None:
        drifted = []
        for item in list(plan.get("changes", [])) + list(plan.get("noops", [])):
            node = current_models.get(item.get("requestModel"))
            if node is None:
                drifted.append(f"{item.get('requestModel')}: model is missing")
            elif settings_fingerprint(node.get("settings")) != item.get(
                "currentSettingsFingerprint"
            ):
                drifted.append(
                    f"{item.get('requestModel')}: settings changed since plan"
                )
        if drifted:
            raise MappingInputError(
                "plan is stale; regenerate dry-run before apply: "
                + "; ".join(drifted)
            )

    applied: list[str] = []
    applied_templates: list[str] = []
    failures: list[dict[str, str]] = []
    for item in plan.get("changes", []):
        try:
            data = _graphql_data(
                axonhub_url,
                token,
                UPDATE_MODEL_MUTATION,
                {
                    "id": item["internalID"],
                    "input": {"settings": item["settings"]},
                },
            )
            updated = data.get("updateModel") or {}
            applied.append(str(updated.get("modelID") or item["requestModel"]))
        except Exception as exc:  # noqa: BLE001 - report one failed model, keep others
            failures.append(
                {
                    "model": item["requestModel"],
                    "message": str(exc),
                }
            )
    template_plan = plan.get("templates") or {}
    for item in template_plan.get("changes", []):
        try:
            _graphql_data(
                axonhub_url,
                token,
                UPDATE_TEMPLATE_MUTATION,
                {
                    "id": item["internalID"],
                    "input": {},
                    "profile": item["profile"],
                },
            )
            applied_templates.append(str(item["name"]))
        except Exception as exc:  # noqa: BLE001
            failures.append({"model": f"template:{item['name']}", "message": str(exc)})
    for item in template_plan.get("creates", []):
        try:
            _graphql_data(
                axonhub_url,
                token,
                CREATE_TEMPLATE_MUTATION,
                {
                    "input": {
                        "name": item["name"],
                        "description": item.get("description", ""),
                        "projectID": item["projectID"],
                    },
                    "profile": item["profile"],
                },
            )
            applied_templates.append(str(item["name"]))
        except Exception as exc:  # noqa: BLE001
            failures.append({"model": f"template:{item['name']}", "message": str(exc)})
    return {
        "applied": applied,
        "appliedTemplates": applied_templates,
        "failures": failures,
    }


def verify_plan(
    axonhub_url: str, token: str, plan: dict[str, Any]
) -> list[dict[str, str]]:
    """Verify each changed request model has exactly the desired association."""

    remote_models = fetch_all_models(axonhub_url, token)
    failures: list[dict[str, str]] = []
    for item in list(plan.get("changes", [])) + list(plan.get("noops", [])):
        node = remote_models.get(item["requestModel"])
        if node is None:
            failures.append(
                {"model": item["requestModel"], "message": "model disappeared after apply"}
            )
            continue
        settings = node.get("settings") or {}
        if not associations_match(settings, item["targetModel"]):
            failures.append(
                {
                    "model": item["requestModel"],
                    "message": "association does not match the confirmed target",
                }
            )
    template_plan = plan.get("templates") or {}
    managed_names = set(plan.get("managedTemplates") or [])
    templates = fetch_managed_templates(axonhub_url, token, managed_names)
    for item in (
        list(template_plan.get("changes", []))
        + list(template_plan.get("creates", []))
        + list(template_plan.get("noops", []))
    ):
        node = templates.get(item["name"])
        if node is None or normalize_profile_for_compare(
            node.get("profile")
        ) != normalize_profile_for_compare(item["profile"]):
            failures.append(
                {
                    "model": f"template:{item['name']}",
                    "message": "template profile does not match the confirmed plan",
                }
            )
    return failures


def print_summary(plan: dict[str, Any]) -> None:
    """Print a compact, secret-free plan summary for a human review."""

    template_plan = plan.get("templates") or {}
    template_rows = []
    for status in ("changes", "creates", "noops"):
        for item in template_plan.get(status, []):
            before = item.get("beforeMappings") or {}
            after = item.get("afterMappings") or {}
            template_rows.append(
                {
                    "template": item["name"],
                    "status": status[:-1] if status.endswith("s") else status,
                    "added": len(set(after) - set(before)),
                    "removed": len(set(before) - set(after)),
                    "changed": sum(
                        1
                        for key in set(before) & set(after)
                        if before[key] != after[key]
                    ),
                    "linkedProfiles": item.get("linkedProfilesCount", 0),
                }
            )
    print(
        json.dumps(
            {
                "associationSummary": {
                    "requestCount": plan.get("requestCount", 0),
                    "changed": len(plan.get("changes", [])),
                    "noop": len(plan.get("noops", [])),
                },
                "planHash": plan.get("planHash"),
                "templateSummary": template_rows,
                "affectedLinkedProfiles": sum(
                    row["linkedProfiles"]
                    for row in template_rows
                    if row["status"] != "noop"
                ),
                "blockingErrorCount": len(plan.get("errors", [])),
                "warningCount": len(plan.get("warnings", []))
                + len(template_plan.get("warnings", [])),
                "changes": [
                    {
                        "requestModel": item["requestModel"],
                        "currentTarget": item["currentTarget"],
                        "targetModel": item["targetModel"],
                    }
                    for item in plan.get("changes", [])
                ],
                "errors": plan.get("errors", []),
                "warnings": list(plan.get("warnings", []))
                + list(template_plan.get("warnings", [])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or apply confirmed request-model associations"
    )
    parser.add_argument(
        "--mapping-file",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "models.csv",
        help="generated mapping workspace (default: repository models.csv)",
    )
    parser.add_argument(
        "--request-models",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "config" / "request-models.json",
        help="fixed request-model configuration",
    )
    parser.add_argument(
        "--model-decisions",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "config"
        / "model-decisions.json",
        help="managed scope and reviewed model decisions",
    )
    parser.add_argument(
        "--axonhub-url",
        default=os.environ.get("AXONHUB_URL", "https://axon.jasonqin.site"),
        help="AxonHub base URL",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("AXONHUB_JWT"),
        help="AxonHub JWT (prefer AXONHUB_JWT; never print it)",
    )
    parser.add_argument(
        "--plan-output",
        type=Path,
        help="optional path for the complete JSON plan",
    )
    parser.add_argument(
        "--plan-input",
        type=Path,
        help="apply a previously reviewed JSON plan instead of rebuilding it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the plan without mutations (default when --apply is absent)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply changes; use only after the mapping table is explicitly confirmed",
    )
    args = parser.parse_args(argv)

    if args.dry_run and args.apply:
        parser.error("use either --dry-run or --apply, not both")
    if not args.token:
        parser.error("provide --token or set AXONHUB_JWT")

    try:
        if args.plan_input:
            with args.plan_input.open(encoding="utf-8") as handle:
                plan = json.load(handle)
            if not isinstance(plan, dict):
                raise MappingInputError("plan input must contain a JSON object")
        else:
            plan = build_plan(
                args.mapping_file,
                args.request_models,
                args.model_decisions,
                args.axonhub_url,
                args.token,
            )
            plan["planHash"] = mapping_plan_hash(plan)
    except (MappingInputError, RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.plan_output:
        args.plan_output.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print_summary(plan)

    if plan.get("errors"):
        return 2
    if not args.apply:
        return 0

    try:
        current_models = fetch_all_models(args.axonhub_url, args.token)
        fixed_request_ids = load_fixed_request_ids(args.request_models)
        managed_template_names = load_managed_template_names(args.model_decisions)
        current_templates = fetch_managed_templates(
            args.axonhub_url, args.token, managed_template_names
        )
        validate_saved_plan(
            plan,
            fixed_request_ids,
            current_models,
            managed_template_names,
            current_templates,
        )
        result = apply_plan(args.axonhub_url, args.token, plan, current_models)
        verification_failures = verify_plan(args.axonhub_url, args.token, plan)
    except (MappingInputError, RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: apply/verify failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"result": result, "verificationFailures": verification_failures}, ensure_ascii=False, indent=2))
    return 1 if result["failures"] or verification_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
