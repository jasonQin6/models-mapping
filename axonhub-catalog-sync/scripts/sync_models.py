#!/usr/bin/env python3
"""Plan and apply one OpenCode provider catalog to AxonHub.

This standalone, dependency-free helper never performs Git operations.  The
caller must review a saved plan and explicitly pass ``--apply``.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = 1
CHANNEL_BY_NPM = {
    "@ai-sdk/openai-compatible": "opencode-go",
    "@ai-sdk/openai": "op-responses",
    "@ai-sdk/anthropic": "op-anthropic",
}
NPM_BY_PROTOCOL = {
    "completions": "@ai-sdk/openai-compatible",
    "responses": "@ai-sdk/openai",
    "messages": "@ai-sdk/anthropic",
}
REMARK_FIELDS = ("rp5h", "usage_quota", "context_threshold", "peak_hours", "retention")


class SyncError(RuntimeError):
    """A user-actionable synchronization failure."""


class BlockingPlanError(SyncError):
    """Raised when a plan cannot safely be applied."""


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _model_id(model: Mapping[str, Any], fallback: str | None = None) -> str:
    value = model.get("id") or model.get("modelID") or model.get("model_id") or fallback
    if value is None or not str(value).strip():
        raise SyncError("source model has no id")
    return str(value)


def _effective_npm(model: Mapping[str, Any], provider_npm: Any) -> str | None:
    api = _as_dict(model.get("api"))
    value = api.get("npm") or model.get("npm") or provider_npm
    return str(value) if value else None


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SyncError(f"source file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"invalid JSON in {path}: {exc}") from exc


def _provider_nodes(document: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(document, Mapping):
        return {}
    providers = document.get("providers")
    if isinstance(providers, Mapping):
        return {str(key): _as_dict(value) for key, value in providers.items()}
    # Raw ~/.cache/opencode/models.json has provider IDs at the top level.
    if document.get("schema_version") is None:
        return {
            str(key): _as_dict(value)
            for key, value in document.items()
            if isinstance(value, Mapping) and isinstance(value.get("models"), (Mapping, list))
        }
    return {}


def find_provider(document: Any, provider: str) -> dict[str, Any]:
    nodes = _provider_nodes(document)
    node = nodes.get(provider)
    if node is None and isinstance(document, Mapping) and document.get("provider") == provider:
        node = dict(document)
    if node is None:
        raise SyncError(f"provider {provider!r} not found in cache")
    return node


def _model_records(provider_node: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = provider_node.get("models", {})
    if isinstance(raw, Mapping):
        records: list[dict[str, Any]] = []
        for key, value in raw.items():
            record = _as_dict(value)
            record.setdefault("id", str(key))
            records.append(record)
        return records
    if isinstance(raw, list):
        return [_as_dict(item) for item in raw if isinstance(item, Mapping)]
    raise SyncError("provider models must be an object or list")


def normalize_provider(provider_node: Mapping[str, Any], provider: str) -> dict[str, Any]:
    """Normalize one raw provider node and make effective npm explicit."""

    provider_npm = provider_node.get("npm")
    records: dict[str, dict[str, Any]] = {}
    for raw_model in _model_records(provider_node):
        model = dict(raw_model)
        model_id = _model_id(model)
        if model_id in records:
            raise SyncError(f"duplicate model {model_id!r} in provider {provider!r}")
        npm = _effective_npm(model, provider_npm)
        if npm:
            model["npm"] = npm
        records[model_id] = model

    normalized: dict[str, Any] = {
        "id": str(provider_node.get("id") or provider),
        "models": {key: records[key] for key in sorted(records)},
    }
    for key in ("env", "npm", "api", "name", "doc"):
        if key in provider_node:
            normalized[key] = provider_node[key]
    for key in sorted(provider_node):
        if key not in normalized and key not in {"models", "env"}:
            value = provider_node[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                normalized[key] = value
    return normalized


def normalize_snapshot(
    cache_path: Path,
    provider: str,
    existing_path: Path | None = None,
) -> dict[str, Any]:
    """Return a deterministic accumulated snapshot for one provider."""

    source = load_json(cache_path)
    provider_node = normalize_provider(find_provider(source, provider), provider)
    existing: dict[str, Any] = {}
    if existing_path and existing_path.exists():
        loaded = load_json(existing_path)
        if isinstance(loaded, Mapping) and isinstance(loaded.get("providers"), Mapping):
            existing = dict(loaded)
    providers = {
        str(key): value
        for key, value in _as_dict(existing.get("providers")).items()
        if isinstance(value, Mapping)
    }
    providers[provider] = provider_node
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {"kind": "opencode-cache", "provider": provider},
        "providers": {key: providers[key] for key in sorted(providers)},
    }


def write_json(path: Path, value: Any) -> None:
    """Write JSON atomically without exposing a partially written snapshot."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def value_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def managed_model_state(model: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: model.get(field)
        for field in (
            "name",
            "developer",
            "icon",
            "group",
            "modelCard",
            "remark",
            "status",
            "settings",
        )
    }


def candidate_channel_association(channel_id: int, model_id: str) -> dict[str, Any]:
    return {
        "type": "channel_model",
        "priority": 0,
        "disabled": False,
        "when": None,
        "channelModel": {"channelId": channel_id, "modelId": model_id},
        "channelRegex": None,
        "regex": None,
        "modelId": None,
        "channelTagsModel": None,
        "channelTagsRegex": None,
    }


def normalize_model_card_for_compare(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    card = copy.deepcopy(dict(value or {}))
    for field in ("knowledge", "releaseDate", "lastUpdated"):
        card[field] = card.get(field) or ""
    return card


def normalize_settings_for_compare(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    settings = copy.deepcopy(dict(value or {}))
    association_fields = (
        "type",
        "priority",
        "disabled",
        "when",
        "channelModel",
        "channelRegex",
        "regex",
        "modelId",
        "channelTagsModel",
        "channelTagsRegex",
    )
    settings["associations"] = [
        {field: dict(item or {}).get(field) for field in association_fields}
        for item in settings.get("associations") or []
    ]
    return settings


def reconcile_candidate_settings(
    settings: Mapping[str, Any] | None,
    *,
    model_id: str,
    channel_id: int,
    managed_channel_ids: set[int],
) -> dict[str, Any]:
    """Replace only managed channel_model rules and preserve external rules."""

    current = dict(settings or {})
    associations = []
    for association in current.get("associations") or []:
        if not isinstance(association, Mapping):
            continue
        channel_model = association.get("channelModel")
        channel_id_value = (
            channel_model.get("channelId")
            if isinstance(channel_model, Mapping)
            else None
        )
        if (
            association.get("type") == "channel_model"
            and channel_id_value in managed_channel_ids
        ):
            continue
        associations.append(dict(association))
    associations.append(candidate_channel_association(channel_id, model_id))
    current["disableDeveloperSettingsInheritance"] = bool(
        current.get("disableDeveloperSettingsInheritance", False)
    )
    current["associations"] = associations
    current["loadBalancerStrategy"] = str(
        current.get("loadBalancerStrategy") or "default"
    )
    current["traceStickyMode"] = str(current.get("traceStickyMode") or "default")
    return current


def _go_model_records(document: Any) -> tuple[str | None, dict[str, dict[str, Any]]]:
    provider: str | None = None
    if isinstance(document, Mapping):
        if document.get("provider"):
            provider = str(document["provider"])
        raw = document.get("models", document)
    else:
        raw = document
    records: dict[str, dict[str, Any]] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if key in {"schema_version", "source", "provider", "fetched_at"}:
                continue
            if isinstance(value, Mapping):
                record = dict(value)
                records[_model_id(record, str(key))] = record
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, Mapping):
                record = dict(value)
                records[_model_id(record)] = record
    return provider, records


def load_go_models(path: Path | None) -> tuple[str | None, dict[str, dict[str, Any]]]:
    if path is None or not path.exists():
        return None, {}
    document = load_json(path)
    if not isinstance(document, Mapping) or document.get("schema_version") != SCHEMA_VERSION:
        raise SyncError("go.json must use schema_version 1")
    if not isinstance(document.get("models"), (Mapping, list)):
        raise SyncError("go.json models must be an object or list")
    return _go_model_records(document)


def load_model_decisions(path: Path, provider: str) -> dict[str, Any]:
    """Load managed scope plus reviewed exclusions/supplements."""

    document = load_json(path)
    if not isinstance(document, Mapping) or document.get("schema_version") != SCHEMA_VERSION:
        raise SyncError("model-decisions.json must use schema_version 1")
    scope = document.get("scope")
    if not isinstance(scope, Mapping):
        raise SyncError("model decisions have no scope")
    channels = scope.get("channels")
    templates = scope.get("templates")
    if (
        not isinstance(channels, list)
        or not channels
        or not all(isinstance(item, str) for item in channels)
        or not isinstance(templates, list)
        or not all(isinstance(item, str) for item in templates)
    ):
        raise SyncError("model decision scope is invalid")
    excluded: dict[str, str] = {}
    supplements: dict[str, dict[str, Any]] = {}
    items = document.get("models")
    if not isinstance(items, list):
        raise SyncError("model decisions models must be a list")
    allowed_fields = set(REMARK_FIELDS)
    for item in items:
        if not isinstance(item, Mapping):
            raise SyncError("model decisions contain an invalid entry")
        if str(item.get("provider") or "") != provider:
            continue
        model_id = str(item.get("model_id") or "").strip()
        reason = str(item.get("reason") or "").strip()
        action = str(item.get("action") or "").strip()
        if not model_id or not reason or model_id in excluded or model_id in supplements:
            raise SyncError("model decisions require unique model_id and reason")
        if action == "exclude":
            excluded[model_id] = reason
        elif action == "supplement":
            fields = item.get("fields")
            if not isinstance(fields, Mapping) or not fields or set(fields) - allowed_fields:
                raise SyncError(f"invalid supplement for {model_id!r}")
            supplements[model_id] = dict(fields)
        else:
            raise SyncError(f"unsupported model decision for {model_id!r}")
    return {
        "channels": list(channels),
        "templates": list(templates),
        "excluded": excluded,
        "supplements": supplements,
    }


def _cost_from_model(model: Mapping[str, Any]) -> dict[str, Any]:
    raw_cost = _as_dict(model.get("cost"))
    nested_cache = _as_dict(raw_cost.get("cache"))
    return {
        "input": raw_cost.get("input", 0),
        "output": raw_cost.get("output", 0),
        "cacheRead": raw_cost.get("cache_read", raw_cost.get("cacheRead", nested_cache.get("read", 0))),
        "cacheWrite": raw_cost.get("cache_write", raw_cost.get("cacheWrite", nested_cache.get("write", 0))),
    }


def model_card(model: Mapping[str, Any], cost_model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Map cache capabilities to AxonHub's modelCard shape."""

    modalities = _as_dict(model.get("modalities"))
    limit = _as_dict(model.get("limit"))
    card_source = cost_model or model
    return {
        "reasoning": {"supported": bool(model.get("reasoning", False)), "default": bool(model.get("reasoning", False))},
        "toolCall": bool(model.get("tool_call", model.get("toolCall", model.get("toolcall", False)))),
        "temperature": bool(model.get("temperature", True)),
        "modalities": {"input": modalities.get("input", ["text"]), "output": modalities.get("output", ["text"])},
        "vision": "image" in modalities.get("input", []),
        "cost": _cost_from_model(card_source),
        "limit": {"context": limit.get("context", 0), "output": limit.get("output", 0)},
        "knowledge": model.get("knowledge") or "",
        "releaseDate": _first(model, "release_date", "releaseDate") or "",
        "lastUpdated": _first(model, "last_updated", "lastUpdated") or "",
    }


def parse_remark(value: Any) -> dict[str, Any]:
    """Decode a remark while preserving legacy free text as manual content."""

    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"manual": value}
        if isinstance(decoded, Mapping):
            return dict(decoded)
        return {"manual": value}
    return {}


def build_remark(existing: Any, go_model: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build the managed remark object and clear missing source values."""

    old = parse_remark(existing)
    result: dict[str, Any] = {"manual": old.get("manual", "")}
    source = go_model or {}
    aliases = {
        "usage_quota": ("usage_quota", "usageQuota"),
        "context_threshold": ("context_threshold", "contextThreshold"),
        "peak_hours": ("peak_hours", "peakHours"),
    }
    for field in REMARK_FIELDS:
        result[field] = _first(source, *aliases.get(field, (field,)))
    return result


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


def _merged_models(
    provider: str,
    provider_node: Mapping[str, Any],
    go_provider: str | None,
    go_models: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cache_models = _model_records(provider_node)
    by_id = {_model_id(model): dict(model) for model in cache_models}
    warnings: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    if provider != "opencode-go":
        return [by_id[key] for key in sorted(by_id)], warnings, excluded
    if go_provider not in (None, "", "opencode-go"):
        raise SyncError("go.json does not describe opencode-go")
    cache_ids = set(by_id)
    go_ids = set(go_models)
    for model_id in sorted(cache_ids - go_ids):
        excluded.append({"modelID": model_id, "reason": "absent_in_go"})
    for model_id in sorted(go_ids - cache_ids):
        excluded.append({"modelID": model_id, "reason": "absent_in_models"})
    included_ids = cache_ids & go_ids
    return [by_id[key] for key in sorted(included_ids)], warnings, excluded


def _fix_free_supplements(
    source_models: Sequence[Mapping[str, Any]],
    go_models: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Derive missing free-model quota fields from the largest Go values."""

    def number(value: Any) -> float:
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return 0.0

    fixed = {str(key): dict(value) for key, value in go_models.items()}
    max_rp5h = max(
        (number(value.get("rp5h")) for value in fixed.values()),
        default=0,
    )
    max_usage = max(
        (number(value.get("usage_quota")) for value in fixed.values()),
        default=0,
    )
    for model in source_models:
        model_id = _model_id(model)
        if "free" not in model_id.lower():
            continue
        record = fixed.setdefault(model_id, {})
        if record.get("rp5h") in (None, "", "-"):
            record["rp5h"] = max_rp5h
        if record.get("usage_quota") in (None, "", "-"):
            record["usage_quota"] = max_usage
    return fixed


class GraphQLClient:
    """Small stdlib GraphQL client that keeps tokens out of process argv."""

    def __init__(
        self,
        url: str,
        auth_token: str,
        timeout: int = 30,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.url = url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self.opener = opener

    def execute(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": dict(variables or {})}, ensure_ascii=False)
        request = urllib.request.Request(
            f"{self.url}/admin/graphql",
            data=payload.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.auth_token,
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except (OSError, urllib.error.URLError) as exc:
            raise SyncError("AxonHub GraphQL request timed out") from exc
        try:
            decoded = json.loads(body)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SyncError("AxonHub returned invalid GraphQL JSON") from exc
        if decoded.get("errors"):
            raise SyncError("AxonHub GraphQL returned an error")
        data = decoded.get("data")
        if not isinstance(data, Mapping):
            raise SyncError("AxonHub GraphQL response has no data")
        return dict(data)


CHANNEL_NODE_SELECTION = "id name status supportedModels defaultTestModel"
MODEL_NODE_SELECTION = (
    "id modelID name developer icon group status remark "
    "modelCard { reasoning { supported default } toolCall temperature "
    "modalities { input output } vision cost { input output cacheRead cacheWrite } "
    "limit { context output } knowledge releaseDate lastUpdated } "
    "settings { disableDeveloperSettingsInheritance loadBalancerStrategy traceStickyMode "
    "associations { type priority disabled "
    "when { enabled condition { type logic field operator value "
    "conditions { type logic field operator value } } } "
    "channelModel { channelId modelId } "
    "channelRegex { channelId pattern } "
    "regex { pattern exclude { channelNamePattern channelIds channelTags } } "
    "modelId { modelId exclude { channelNamePattern channelIds channelTags } } "
    "channelTagsModel { channelTags modelId } "
    "channelTagsRegex { channelTags pattern } } }"
)


def paged_nodes(
    client: GraphQLClient,
    field: str,
    node_selection: str,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Fetch all Relay pages and reject a repeated or malformed cursor."""

    query = (
        f"query Paged{field.title()}($first: Int!, $after: Cursor) {{ "
        f"{field}(first: $first, after: $after) {{ edges {{ node {{ {node_selection} }} }} "
        "pageInfo { hasNextPage endCursor } } }"
    )
    after: str | None = None
    seen: set[str] = set()
    result_nodes: list[dict[str, Any]] = []
    while True:
        data = client.execute(query, {"first": page_size, "after": after})
        connection = data.get(field)
        if not isinstance(connection, Mapping):
            raise SyncError(f"AxonHub GraphQL response has no {field} connection")
        edges = connection.get("edges")
        page_info = connection.get("pageInfo")
        if not isinstance(edges, list) or not isinstance(page_info, Mapping):
            raise SyncError(f"AxonHub {field} response is not paginated")
        for edge in edges:
            if isinstance(edge, Mapping) and isinstance(edge.get("node"), Mapping):
                result_nodes.append(dict(edge["node"]))
        if not page_info.get("hasNextPage"):
            return result_nodes
        next_cursor = page_info.get("endCursor")
        if not next_cursor or str(next_cursor) in seen or str(next_cursor) == str(after):
            raise SyncError(f"AxonHub {field} pagination returned a repeated cursor")
        seen.add(str(next_cursor))
        after = str(next_cursor)


def fetch_channels(client: GraphQLClient, page_size: int = 100) -> dict[str, dict[str, Any]]:
    nodes = paged_nodes(client, "channels", CHANNEL_NODE_SELECTION, page_size)
    return {str(node["name"]): node for node in nodes if node.get("name")}


def fetch_models(client: GraphQLClient, page_size: int = 100) -> dict[str, dict[str, Any]]:
    nodes = paged_nodes(client, "models", MODEL_NODE_SELECTION, page_size)
    result: dict[str, dict[str, Any]] = {}
    for node in nodes:
        model_id = node.get("modelID")
        if model_id:
            result[str(model_id)] = node
    return result


def _source_cost_models(cache_document: Any) -> dict[str, dict[str, Any]]:
    opencode = _provider_nodes(cache_document).get("opencode-go")
    if not opencode:
        return {}
    return {_model_id(model): model for model in _model_records(opencode)}


def build_plan(
    cache_path: Path,
    provider: str,
    go_path: Path | None,
    client: GraphQLClient,
    *,
    decisions_path: Path | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Build a safe, JSON-serializable plan from sources and AxonHub."""

    cache_document = load_json(cache_path)
    provider_node = find_provider(cache_document, provider)
    normalized_provider = normalize_provider(provider_node, provider)
    go_provider, go_models = load_go_models(go_path)
    if decisions_path is None:
        raise SyncError("model decisions path is required")
    decisions = load_model_decisions(decisions_path, provider)
    source_models, source_warnings, excluded = _merged_models(
        provider, provider_node, go_provider, go_models
    )
    for model_id, fields in decisions["supplements"].items():
        go_models.setdefault(model_id, {}).update(fields)
    manual_excluded = decisions["excluded"]
    for model_id, reason in sorted(manual_excluded.items()):
        if any(_model_id(model) == model_id for model in source_models):
            excluded.append(
                {"modelID": model_id, "reason": "manual_exclude", "detail": reason}
            )
    source_models = [
        model for model in source_models if _model_id(model) not in manual_excluded
    ]
    go_models = _fix_free_supplements(source_models, go_models)
    if not source_models:
        raise SyncError(f"provider {provider!r} has no models")
    cache_model_ids = {
        _model_id(model) for model in _model_records(provider_node)
    }

    channels = fetch_channels(client, page_size)
    existing = fetch_models(client, page_size)
    grouped: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    warnings = list(source_warnings)
    decision_required: list[dict[str, Any]] = []
    if go_path is not None and not go_path.exists():
        warnings.append({"type": "go_source_missing", "path": str(go_path)})
    model_channels: dict[str, str] = {}
    for model in source_models:
        model_id = _model_id(model)
        if provider == "opencode-go" and go_models.get(model_id, {}).get("rp5h") in (
            None,
            "",
            "-",
        ):
            decision_required.append(
                {"type": "missing_rp5h", "model": model_id}
            )
        api_npm = _as_dict(model.get("api")).get("npm")
        go_protocol = str((go_models.get(model_id) or {}).get("protocol") or "").lower()
        if provider == "opencode-go":
            protocol_npm = NPM_BY_PROTOCOL.get(go_protocol)
            if not protocol_npm:
                errors.append({"type": "missing_protocol", "model": model_id})
                continue
            if api_npm and str(api_npm) != protocol_npm:
                errors.append(
                    {
                        "type": "protocol_npm_conflict",
                        "model": model_id,
                        "apiNpm": str(api_npm),
                        "protocolNpm": protocol_npm,
                    }
                )
                continue
            npm = protocol_npm
        else:
            npm = _effective_npm(model, normalized_provider.get("npm"))
        if not npm:
            errors.append({"type": "missing_npm", "model": model_id})
            continue
        channel_name = CHANNEL_BY_NPM.get(npm)
        if channel_name is None:
            errors.append({"type": "unsupported_npm", "model": model_id, "npm": npm})
            continue
        model_channels[model_id] = channel_name
        grouped.setdefault(channel_name, []).append(model)

    enabled_channels: dict[str, dict[str, Any]] = {}
    skipped_channels: list[dict[str, Any]] = []
    managed_channel_names = set(decisions["channels"])
    for channel_name, models in sorted(grouped.items()):
        if channel_name not in managed_channel_names:
            errors.append(
                {"type": "channel_outside_managed_scope", "channel": channel_name}
            )
            continue
        node = channels.get(channel_name)
        if node is None:
            errors.append({"type": "missing_channel", "channel": channel_name, "models": len(models)})
        elif str(node.get("status", "")).lower() != "enabled":
            skipped_channels.append({"channel": channel_name, "models": len(models), "status": node.get("status")})
            warnings.append({"type": "disabled_channel", "channel": channel_name, "models": len(models)})
        else:
            enabled_channels[channel_name] = node
    if grouped and not enabled_channels:
        errors.append({"type": "no_enabled_channel"})

    managed_channel_ids = {
        int(str(node["id"]).rsplit("/", 1)[-1])
        for name, node in channels.items()
        if name in managed_channel_names
    }

    channel_updates: list[dict[str, Any]] = []
    for channel_name, node in sorted(enabled_channels.items()):
        models = sorted(grouped[channel_name], key=lambda item: _model_id(item))
        after = [_model_id(model) for model in models]
        before = list(node.get("supportedModels") or [])
        default_test_model = node.get("defaultTestModel")
        if default_test_model and default_test_model not in after:
            errors.append(
                {
                    "type": "default_test_model_removed",
                    "channel": channel_name,
                    "model": default_test_model,
                }
            )
            continue
        if before != after:
            channel_updates.append({
                "channel": channel_name,
                "channelId": str(node["id"]),
                "before": before,
                "after": after,
                "input": {"supportedModels": after},
            })

    opencode_costs = _source_cost_models(cache_document)
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    deletes: list[dict[str, Any]] = []
    externally_retained: list[dict[str, Any]] = []
    blocking_references: list[dict[str, Any]] = []
    excluded_ids = {str(item["modelID"]) for item in excluded}
    for model_id in sorted(excluded_ids):
        external_channels = sorted(
            name
            for name, node in channels.items()
            if name not in managed_channel_names
            and model_id in (node.get("supportedModels") or [])
        )
        references = []
        for owner_id, owner in existing.items():
            settings = owner.get("settings") or {}
            for association in settings.get("associations") or []:
                if not isinstance(association, Mapping):
                    continue
                channel_model = association.get("channelModel")
                model_target = association.get("modelId")
                target = None
                if isinstance(channel_model, Mapping):
                    target = channel_model.get("modelId")
                if isinstance(model_target, Mapping):
                    target = model_target.get("modelId")
                if target == model_id and owner_id != model_id:
                    references.append(
                        {"owner": owner_id, "type": association.get("type")}
                    )
        if external_channels:
            externally_retained.append(
                {
                    "modelID": model_id,
                    "externalChannels": external_channels,
                    "references": references,
                }
            )
            continue
        if references:
            blocking_references.append(
                {"modelID": model_id, "references": references}
            )
            continue
        old = existing.get(model_id)
        if old is not None:
            deletes.append(
                {
                    "modelID": model_id,
                    "internalID": str(old["id"]),
                    "reason": next(
                        item.get("reason", "excluded")
                        for item in excluded
                        if item["modelID"] == model_id
                    ),
                    "beforeFingerprint": value_fingerprint(
                        managed_model_state(old)
                    ),
                }
            )
    if blocking_references:
        errors.append(
            {
                "type": "excluded_models_still_referenced",
                "models": [item["modelID"] for item in blocking_references],
            }
        )
    for model in source_models:
        model_id = _model_id(model)
        channel_name = model_channels.get(model_id)
        if channel_name not in enabled_channels:
            continue
        channel_id = int(
            str(enabled_channels[channel_name]["id"]).rsplit("/", 1)[-1]
        )
        developer, icon, group = _model_meta(model, provider)
        is_cache_model = model_id in cache_model_ids
        card = model_card(model, opencode_costs.get(model_id)) if is_cache_model else None
        new_remark = remark_json(build_remark(existing.get(model_id, {}).get("remark"), go_models.get(model_id)))
        managed = {
            "developer": developer,
            "name": str(model.get("name") or model_id),
            "icon": icon,
            "group": group,
            "remark": new_remark,
            "settings": reconcile_candidate_settings(
                existing.get(model_id, {}).get("settings"),
                model_id=model_id,
                channel_id=channel_id,
                managed_channel_ids=managed_channel_ids,
            ),
        }
        if card is not None:
            managed["modelCard"] = card
        old = existing.get(model_id)
        if old is None:
            creates.append({
                "modelID": model_id,
                "channel": channel_name,
                "input": {
                    "modelID": model_id,
                    "name": managed["name"],
                    "developer": developer,
                    "type": "chat",
                    "icon": icon,
                    "group": group,
                    "remark": new_remark,
                    "settings": managed["settings"],
                },
            })
            if card is not None:
                creates[-1]["input"]["modelCard"] = card
            continue
        changed_input = {}
        for field, desired in managed.items():
            actual = old.get(field)
            if field == "modelCard":
                equal = normalize_model_card_for_compare(actual) == normalize_model_card_for_compare(desired)
            elif field == "settings":
                equal = normalize_settings_for_compare(actual) == normalize_settings_for_compare(desired)
            else:
                equal = actual == desired
            if not equal:
                changed_input[field] = desired
        if changed_input:
            updates.append({
                "modelID": model_id,
                "internalID": str(old["id"]),
                "beforeFingerprint": value_fingerprint(managed_model_state(old)),
                "input": changed_input,
            })
    deletion_guard = value_fingerprint(
        {
            "channels": {
                name: list(node.get("supportedModels") or [])
                for name, node in sorted(channels.items())
            },
            "associations": {
                model_id: (node.get("settings") or {}).get("associations") or []
                for model_id, node in sorted(existing.items())
            },
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "sourceFingerprints": {
            "cache": file_sha256(cache_path),
            "go": file_sha256(go_path) if go_path and go_path.exists() else None,
            "decisions": file_sha256(decisions_path),
        },
        "sourceCount": len(source_models),
        "cacheCount": len(_model_records(provider_node)),
        "goCount": len(go_models),
        "enabledChannels": sorted(enabled_channels),
        "skippedChannels": skipped_channels,
        "warnings": warnings,
        "decisionRequired": decision_required,
        "errors": errors,
        "included": sorted(_model_id(model) for model in source_models),
        "excluded": sorted(excluded, key=lambda item: item["modelID"]),
        "managedChannels": list(decisions["channels"]),
        "externallyRetained": externally_retained,
        "blockingReferences": blocking_references,
        "channelUpdates": channel_updates,
        "creates": creates,
        "updates": updates,
        "deletes": deletes,
        "deletionGuardFingerprint": deletion_guard,
    }


CREATE_MODEL_MUTATION = """
mutation CreateModel($input: CreateModelInput!) {
  createModel(input: $input) { id modelID }
}
"""
UPDATE_MODEL_MUTATION = """
mutation UpdateModel($id: ID!, $input: UpdateModelInput!) {
  updateModel(id: $id, input: $input) { id modelID status }
}
"""
UPDATE_CHANNEL_MUTATION = """
mutation UpdateChannel($id: ID!, $input: UpdateChannelInput!) {
  updateChannel(id: $id, input: $input) { id name supportedModels }
}
"""
DELETE_MODEL_MUTATION = """
mutation DeleteModel($id: ID!) {
  deleteModel(id: $id)
}
"""


def validate_plan_shape(plan: Mapping[str, Any]) -> None:
    """Reject malformed or over-broad saved plans before any mutation."""

    if plan.get("schema_version") != SCHEMA_VERSION:
        raise BlockingPlanError("unsupported sync plan schema_version")
    if not isinstance(plan.get("provider"), str) or not plan["provider"]:
        raise BlockingPlanError("sync plan has no provider")
    fingerprints = plan.get("sourceFingerprints")
    if not isinstance(fingerprints, Mapping) or not fingerprints.get("cache"):
        raise BlockingPlanError("sync plan has no source fingerprints")
    snapshot = plan.get("snapshot")
    if snapshot is not None:
        providers = snapshot.get("providers") if isinstance(snapshot, Mapping) else None
        if (
            snapshot.get("schema_version") != SCHEMA_VERSION
            or not isinstance(providers, Mapping)
            or plan["provider"] not in providers
        ):
            raise BlockingPlanError("sync plan contains an invalid normalized snapshot")
    for field in (
        "creates",
        "updates",
        "deletes",
        "channelUpdates",
        "errors",
        "warnings",
        "decisionRequired",
        "excluded",
        "externallyRetained",
        "blockingReferences",
    ):
        if not isinstance(plan.get(field), list):
            raise BlockingPlanError(f"sync plan field {field} must be a list")
    allowed_model_fields = {
        "modelID",
        "name",
        "developer",
        "type",
        "icon",
        "group",
        "modelCard",
        "remark",
        "settings",
    }
    allowed_update_fields = allowed_model_fields - {"modelID", "type"}
    for item in plan["creates"]:
        if not isinstance(item, Mapping) or set(item.get("input", {})) - allowed_model_fields:
            raise BlockingPlanError("sync plan contains an invalid create input")
    for item in plan["updates"]:
        if not isinstance(item, Mapping) or set(item.get("input", {})) - allowed_update_fields:
            raise BlockingPlanError("sync plan contains an invalid model update input")
        if not item.get("beforeFingerprint"):
            raise BlockingPlanError("sync plan model update has no before fingerprint")
    for item in plan["deletes"]:
        if (
            not isinstance(item, Mapping)
            or not item.get("modelID")
            or not item.get("internalID")
            or not item.get("reason")
            or not item.get("beforeFingerprint")
        ):
            raise BlockingPlanError("sync plan contains an invalid model deletion")
    for item in plan["channelUpdates"]:
        if not isinstance(item, Mapping) or set(item.get("input", {})) != {"supportedModels"}:
            raise BlockingPlanError("sync plan contains an invalid channel update input")


def validate_remote_state(
    client: GraphQLClient,
    plan: Mapping[str, Any],
    *,
    page_size: int = 100,
) -> None:
    """Ensure every reviewed before-state is still current."""

    current_models = fetch_models(client, page_size)
    current_channels = fetch_channels(client, page_size)
    drift: list[str] = []
    for item in plan.get("creates", []):
        if str(item.get("modelID")) in current_models:
            drift.append(f"model {item.get('modelID')} now exists")
    for item in plan.get("updates", []):
        model = current_models.get(str(item.get("modelID")))
        if model is None:
            drift.append(f"model {item.get('modelID')} disappeared")
            continue
        if str(model.get("id")) != str(item.get("internalID")):
            drift.append(f"model {item.get('modelID')} internal ID changed")
        elif value_fingerprint(managed_model_state(model)) != item.get("beforeFingerprint"):
            drift.append(f"model {item.get('modelID')} changed after review")
    for item in plan.get("deletes", []):
        model = current_models.get(str(item.get("modelID")))
        if model is None:
            drift.append(f"model {item.get('modelID')} disappeared")
            continue
        if str(model.get("id")) != str(item.get("internalID")):
            drift.append(f"model {item.get('modelID')} internal ID changed")
        elif value_fingerprint(managed_model_state(model)) != item.get("beforeFingerprint"):
            drift.append(f"model {item.get('modelID')} changed after review")
    if plan.get("deletes"):
        current_guard = value_fingerprint(
            {
                "channels": {
                    name: list(node.get("supportedModels") or [])
                    for name, node in sorted(current_channels.items())
                },
                "associations": {
                    model_id: (node.get("settings") or {}).get("associations") or []
                    for model_id, node in sorted(current_models.items())
                },
            }
        )
        if current_guard != plan.get("deletionGuardFingerprint"):
            drift.append("channel/model references changed after deletion review")
    for item in plan.get("channelUpdates", []):
        channel = current_channels.get(str(item.get("channel")))
        if channel is None:
            drift.append(f"channel {item.get('channel')} disappeared")
        elif str(channel.get("id")) != str(item.get("channelId")):
            drift.append(f"channel {item.get('channel')} internal ID changed")
        elif list(channel.get("supportedModels") or []) != list(item.get("before") or []):
            drift.append(f"channel {item.get('channel')} changed after review")
    if drift:
        raise BlockingPlanError("sync plan is stale: " + "; ".join(drift))


def validate_source_files(
    plan: Mapping[str, Any],
    cache_path: Path,
    go_path: Path | None,
    decisions_path: Path,
) -> None:
    expected = plan.get("sourceFingerprints")
    if not isinstance(expected, Mapping):
        raise BlockingPlanError("sync plan has no source fingerprints")
    actual_cache = file_sha256(cache_path)
    actual_go = file_sha256(go_path) if go_path and go_path.exists() else None
    actual_decisions = file_sha256(decisions_path)
    if (
        expected.get("cache") != actual_cache
        or expected.get("go") != actual_go
        or expected.get("decisions") != actual_decisions
    ):
        raise BlockingPlanError("sync sources changed after review; regenerate the plan")


def apply_plan(
    client: GraphQLClient,
    plan: Mapping[str, Any],
    *,
    page_size: int = 100,
) -> dict[str, Any]:
    """Apply a reviewed plan without touching model settings/associations."""

    validate_plan_shape(plan)
    if plan.get("errors") or plan.get("decisionRequired"):
        raise BlockingPlanError("plan contains blocking errors; apply was refused")
    validate_remote_state(client, plan, page_size=page_size)
    created: list[str] = []
    enabled: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []
    updated_channels: list[str] = []
    for item in plan.get("creates", []):
        response = client.execute(CREATE_MODEL_MUTATION, {"input": item["input"]})
        node = _as_dict(response.get("createModel"))
        internal_id = node.get("id")
        if not internal_id:
            raise SyncError(f"createModel returned no id for {item.get('modelID')}")
        model_id = str(item["modelID"])
        created.append(model_id)
        client.execute(UPDATE_MODEL_MUTATION, {"id": internal_id, "input": {"status": "enabled"}})
        enabled.append(model_id)
    for item in plan.get("updates", []):
        client.execute(UPDATE_MODEL_MUTATION, {"id": item["internalID"], "input": item["input"]})
        updated.append(str(item["modelID"]))
    for item in plan.get("channelUpdates", []):
        client.execute(UPDATE_CHANNEL_MUTATION, {"id": item["channelId"], "input": item["input"]})
        updated_channels.append(str(item["channel"]))
    for item in plan.get("deletes", []):
        client.execute(DELETE_MODEL_MUTATION, {"id": item["internalID"]})
        deleted.append(str(item["modelID"]))
    return {
        "created": created,
        "enabled": enabled,
        "updated": updated,
        "deleted": deleted,
        "updatedChannels": updated_channels,
    }


def verify_plan(client: GraphQLClient, plan: Mapping[str, Any], *, page_size: int = 100) -> dict[str, Any]:
    """Verify desired model/channel values after apply."""

    actual_models = fetch_models(client, page_size)
    actual_channels = fetch_channels(client, page_size)
    failures: list[dict[str, Any]] = []
    expected_models: dict[str, dict[str, Any]] = {}
    new_models: set[str] = set()
    for item in plan.get("creates", []):
        expected_models[str(item["modelID"])] = dict(item["input"])
        new_models.add(str(item["modelID"]))
    for item in plan.get("updates", []):
        expected_models[str(item["modelID"])] = dict(item["input"])
    for model_id, expected in expected_models.items():
        actual = actual_models.get(model_id)
        if actual is None:
            failures.append({"type": "missing_model", "model": model_id})
            continue
        for field, desired in expected.items():
            if field == "modelID":
                continue
            actual_value = actual.get(field)
            if field == "modelCard":
                matches = normalize_model_card_for_compare(actual_value) == normalize_model_card_for_compare(desired)
            elif field == "settings":
                matches = normalize_settings_for_compare(actual_value) == normalize_settings_for_compare(desired)
            else:
                matches = actual_value == desired
            if not matches:
                failures.append({"type": "model_mismatch", "model": model_id, "field": field})
        if model_id in new_models and str(actual.get("status", "")).lower() != "enabled":
            failures.append({"type": "new_model_not_enabled", "model": model_id})
    for item in plan.get("channelUpdates", []):
        actual = actual_channels.get(str(item["channel"]))
        if actual is None:
            failures.append({"type": "missing_channel", "channel": item["channel"]})
        elif list(actual.get("supportedModels") or []) != list(item["after"]):
            failures.append({"type": "channel_mismatch", "channel": item["channel"]})
    for item in plan.get("deletes", []):
        if str(item["modelID"]) in actual_models:
            failures.append({"type": "model_not_deleted", "model": item["modelID"]})
    return {"ok": not failures, "failures": failures}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def mint_local_jwt(db_path: Path, lifetime_seconds: int = 600) -> str:
    """Mint a short-lived JWT from local SQLite without exposing its secret."""

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "select value from systems where key = 'system_jwt_secret_key'"
            ).fetchone()
    except sqlite3.Error as exc:
        raise SyncError("could not read local AxonHub JWT secret") from exc
    if not row or not row[0]:
        raise SyncError("local AxonHub JWT secret is unavailable")
    secret = str(row[0]).encode("utf-8")
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"user_id": 1, "exp": int(time.time()) + lifetime_seconds}, separators=(",", ":")).encode())
    unsigned = f"{header}.{claims}"
    signature = _b64(hmac.new(secret, unsigned.encode("ascii"), hashlib.sha256).digest())
    return f"{unsigned}.{signature}"


def auth_token(db_path: Path) -> str:
    env_token = os.environ.get("AXONHUB_JWT")
    if env_token:
        return env_token
    return mint_local_jwt(db_path)


def refresh_provider(provider: str, cache_path: Path) -> None:
    """Refresh an existing provider and re-check it in the resulting cache."""

    find_provider(load_json(cache_path), provider)
    try:
        result = subprocess.run(
            ["opencode", "models", provider, "--refresh", "--verbose"],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError(f"failed to refresh OpenCode provider {provider!r}") from exc
    if result.returncode != 0:
        raise SyncError(f"OpenCode provider refresh failed for {provider!r}")
    if not cache_path.is_file() or cache_path.stat().st_size == 0:
        raise SyncError(f"OpenCode cache is empty after refreshing {provider!r}")
    find_provider(load_json(cache_path), provider)


def plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": plan.get("provider"),
        "sourceCount": plan.get("sourceCount", 0),
        "enabledChannels": plan.get("enabledChannels", []),
        "warningCount": len(plan.get("warnings") or []),
        "decisionRequiredCount": len(plan.get("decisionRequired") or []),
        "excludedCount": len(plan.get("excluded") or []),
        "externallyRetainedCount": len(plan.get("externallyRetained") or []),
        "errorCount": len(plan.get("errors") or []),
        "createCount": len(plan.get("creates") or []),
        "updateCount": len(plan.get("updates") or []),
        "deleteCount": len(plan.get("deletes") or []),
        "channelUpdateCount": len(plan.get("channelUpdates") or []),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan/apply one OpenCode provider to AxonHub")
    parser.add_argument("--provider")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/opencode/models.json")
    parser.add_argument("--go", type=Path, default=Path("data/go.json"))
    parser.add_argument(
        "--model-decisions",
        type=Path,
        default=Path("config/model-decisions.json"),
    )
    parser.add_argument("--snapshot-output", type=Path)
    parser.add_argument("--axonhub-url", default=os.environ.get("AXONHUB_URL", "https://axon.jasonqin.site"))
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(
            os.environ.get(
                "AXONHUB_DB",
                str(Path.home() / ".config" / "axonhub" / "axonhub.db"),
            )
        ),
    )
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--plan-input", type=Path)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--refresh", action="store_true", help="run opencode models <provider> --refresh --verbose first")
    parser.add_argument("--apply", action="store_true", help="apply an explicitly reviewed plan")
    parser.add_argument("--verify", action="store_true", help="verify AxonHub after apply")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.provider and not args.plan_input:
        print("--provider is required unless --plan-input is supplied", file=sys.stderr)
        return 2
    try:
        token = auth_token(args.db)
        client = GraphQLClient(args.axonhub_url, token)
        if args.plan_input:
            plan = load_json(args.plan_input)
            if not isinstance(plan, Mapping):
                raise SyncError("plan input must be a JSON object")
            validate_plan_shape(plan)
        else:
            if args.refresh:
                refresh_provider(args.provider, args.cache)
            plan = build_plan(
                args.cache,
                args.provider,
                args.go,
                client,
                decisions_path=args.model_decisions,
                page_size=args.page_size,
            )
            if args.snapshot_output:
                plan["snapshot"] = normalize_snapshot(
                    args.cache,
                    args.provider,
                    args.snapshot_output,
                )
            if args.plan_output:
                write_json(args.plan_output, plan)
        print(json.dumps(plan_summary(plan), ensure_ascii=False, indent=2, sort_keys=True))
        if plan.get("errors") or plan.get("decisionRequired"):
            print(
                json.dumps(
                    {
                        "errors": plan.get("errors", []),
                        "decisionRequired": plan.get("decisionRequired", []),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 2
        if args.apply:
            if not args.verify:
                raise SyncError("--apply requires --verify")
            validate_source_files(
                plan, args.cache, args.go, args.model_decisions
            )
            result = apply_plan(client, plan, page_size=args.page_size)
            print(json.dumps({"apply": result}, ensure_ascii=False, indent=2, sort_keys=True))
            verification = verify_plan(client, plan, page_size=args.page_size)
            print(json.dumps({"verify": verification}, ensure_ascii=False, indent=2, sort_keys=True))
            if not verification["ok"]:
                return 3
            if args.snapshot_output:
                snapshot = plan.get("snapshot")
                if not isinstance(snapshot, Mapping):
                    raise SyncError("reviewed plan has no normalized snapshot")
                write_json(args.snapshot_output, snapshot)
        elif args.verify:
            raise SyncError("--verify requires --apply")
        return 0
    except SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
