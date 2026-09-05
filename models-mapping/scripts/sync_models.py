#!/usr/bin/env python3
"""Plan one provider's catalog from a source JSON for AxonHub.

The source is any api.json-shaped document (``{"<provider>": {"models": ...}}``)
such as ``data/goat-models.json`` or ``data/opencode-go-models.json``.  This
standalone, dependency-free helper only produces a reviewed plan file: it never
mutates AxonHub, never runs Git operations, and holds no credential policy of
its own — pass a read token via ``AXONHUB_JWT`` or ``--token``.  Execution
belongs to the ``axonhub-admin`` skill (``apply_catalog_plan.py``).

The managed provider→channel scope comes from ``model-decisions.json``:
providers route to their configured channel by default, and a source provider
or ``--provider-channel`` selection outside that scope is rejected.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = 1
REMARK_FIELDS = ("rp5h", "usage_quota", "context_threshold", "peak_hours", "retention")
REMARK_ALIASES = {
    "usage_quota": ("usage_quota", "usageQuota"),
    "context_threshold": ("context_threshold", "contextThreshold"),
    "peak_hours": ("peak_hours", "peakHours"),
}


class SyncError(RuntimeError):
    """A user-actionable synchronization failure."""


class BlockingPlanError(SyncError):
    """Raised when a plan cannot safely be executed."""


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


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SyncError(f"source file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"invalid JSON in {path}: {exc}") from exc


def find_provider(document: Any, provider: str) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise SyncError("source document must be a JSON object")
    node = document.get(provider)
    if node is None and document.get("provider") == provider:
        node = dict(document)
    if not isinstance(node, Mapping):
        raise SyncError(f"provider {provider!r} not found in source")
    return dict(node)


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
) -> dict[str, Any]:
    """Replace only this channel's channel_model rule and preserve external rules."""

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
        if association.get("type") == "channel_model" and channel_id_value == channel_id:
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


def load_managed_scope(path: Path) -> dict[str, Any]:
    """Load the managed provider→channel scope from model-decisions.json.

    The scope is the single source of truth for which channels this project
    plans; scripts default to it and CLI selections may only stay within it.
    """

    document = load_json(path)
    if not isinstance(document, Mapping) or document.get("schema_version") != SCHEMA_VERSION:
        raise SyncError("model-decisions.json must use schema_version 1")
    scope = document.get("scope")
    if not isinstance(scope, Mapping):
        raise SyncError("model-decisions.json scope must be an object")
    channels = scope.get("channels")
    if not isinstance(channels, Mapping):
        raise SyncError(
            "model-decisions.json scope.channels must be a provider→channel mapping"
        )
    managed: dict[str, str] = {}
    for provider, channel in channels.items():
        if (
            not isinstance(provider, str)
            or not isinstance(channel, str)
            or not provider.strip()
            or not channel.strip()
        ):
            raise SyncError(
                "model-decisions.json scope.channels must map non-empty provider "
                "strings to non-empty channel strings"
            )
        managed[provider.strip()] = channel.strip()
    templates = scope.get("templates")
    if not isinstance(templates, list) or not all(
        isinstance(item, str) and item.strip() for item in templates
    ):
        raise SyncError(
            "model-decisions.json scope.templates must be a list of strings"
        )
    return {"channels": managed, "templates": list(templates)}


def managed_channel(provider: str, scope: Mapping[str, Any]) -> str:
    """Return the managed channel for a provider or raise an out-of-scope error."""

    channels: Mapping[str, str] = scope["channels"]
    channel = channels.get(provider)
    if channel is None:
        raise SyncError(
            f"provider {provider!r} is not in the managed scope "
            f"{sorted(channels)}; planning is limited to managed providers"
        )
    return channel


def validate_provider_channels_in_scope(
    requested: Mapping[str, str],
    scope: Mapping[str, Any],
) -> None:
    """Reject CLI provider→channel selections outside the managed scope."""

    for provider, channel in requested.items():
        expected = managed_channel(provider, scope)
        if channel != expected:
            raise SyncError(
                f"provider→channel {provider}={channel} is outside the managed "
                f"scope; config maps {provider!r} to {expected!r}"
            )


def load_model_decisions(path: Path, provider: str) -> dict[str, Any]:
    """Load reviewed exclusions/supplements for one provider."""

    document = load_json(path)
    if not isinstance(document, Mapping) or document.get("schema_version") != SCHEMA_VERSION:
        raise SyncError("model-decisions.json must use schema_version 1")
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
        "excluded": excluded,
        "supplements": supplements,
    }


def _fix_free_records(
    records: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Derive missing free-model quota fields from channel values.

    Per channel: a free model missing rp5h copies the largest rp5h among the
    channel's non-free models; a missing usage_quota becomes 60.
    """

    def number(value: Any) -> float:
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return 0.0

    fixed = {str(key): dict(value) for key, value in records.items()}
    max_rp5h = max(
        (number(value.get("rp5h")) for model_id, value in fixed.items() if "free" not in model_id.lower()),
        default=0,
    )
    derived: dict[str, list[str]] = {}
    for model_id, record in fixed.items():
        if "free" not in model_id.lower():
            continue
        filled: list[str] = []
        if record.get("rp5h") in (None, "", "-"):
            record["rp5h"] = max_rp5h
            filled.append("rp5h")
        if record.get("usage_quota") in (None, "", "-"):
            record["usage_quota"] = 60
            filled.append("usage_quota")
        if filled:
            derived[model_id] = filled
    return fixed, derived


def _cost_from_model(model: Mapping[str, Any]) -> dict[str, Any]:
    raw_cost = _as_dict(model.get("cost"))
    nested_cache = _as_dict(raw_cost.get("cache"))
    return {
        "input": raw_cost.get("input", 0),
        "output": raw_cost.get("output", 0),
        "cacheRead": raw_cost.get("cache_read", raw_cost.get("cacheRead", nested_cache.get("read", 0))),
        "cacheWrite": raw_cost.get("cache_write", raw_cost.get("cacheWrite", nested_cache.get("write", 0))),
    }


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
        "releaseDate": _first(model, "release_date", "releaseDate") or "",
        "lastUpdated": _first(model, "last_updated", "lastUpdated") or "",
    }


def _remark_values(model: Mapping[str, Any]) -> dict[str, Any]:
    """Read remark fields from extra first, then the model top level."""

    extra = _as_dict(model.get("extra"))
    values: dict[str, Any] = {}
    for field in REMARK_FIELDS:
        aliases = REMARK_ALIASES.get(field, (field,))
        value = _first(extra, *aliases)
        if value is None:
            value = _first(model, *aliases)
        values[field] = value
    return values


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


def build_remark(existing: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """Build the managed remark object and clear missing source values."""

    old = parse_remark(existing)
    result: dict[str, Any] = {"manual": old.get("manual", "")}
    for field in REMARK_FIELDS:
        result[field] = values.get(field)
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


def _append_only_channel(channel: str) -> bool:
    """Channels whose supportedModels carry vendor-prefixed upstream IDs."""

    return channel == "commandcode"


def _git_previous_blob(path: Path) -> str | None:
    """Return the previous committed version of a file as text, or None."""

    try:
        result = subprocess.run(
            ["git", "show", f"HEAD~1:{path.as_posix()}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def change_report(
    all_models_path: Path,
    snapshot_paths: Sequence[Path],
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Diff inputs against their previous committed versions.

    Added/removed models come from all_models.json; price changes come from
    each snapshot's per-model ``cost`` object.  Both compare the working tree
    against ``HEAD~1`` (the last committed version).
    """

    report: dict[str, Any] = {
        "addedModels": [],
        "removedModels": [],
        "priceChanges": [],
        "baseline": "HEAD~1",
    }
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    def previous(path: Path) -> Any:
        text = _git_previous_blob(path)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    all_models = load_json(all_models_path) if all_models_path.exists() else {}
    all_models_prev = previous(all_models_path)
    if isinstance(all_models, Mapping) and isinstance(all_models_prev, Mapping):
        current_ids = set(all_models)
        previous_ids = set(all_models_prev)
        report["addedModels"] = sorted(current_ids - previous_ids)
        report["removedModels"] = sorted(previous_ids - current_ids)

    for snapshot_path in snapshot_paths:
        snapshot = load_json(snapshot_path) if snapshot_path.exists() else {}
        snapshot_prev = previous(snapshot_path)
        if not (isinstance(snapshot, Mapping) and isinstance(snapshot_prev, Mapping)):
            continue
        for provider, node in snapshot.items():
            if not isinstance(node, Mapping):
                continue
            prev_node = snapshot_prev.get(provider)
            if not isinstance(prev_node, Mapping):
                continue
            current_models = node.get("models") or {}
            previous_models = prev_node.get("models") or {}
            if not (isinstance(current_models, Mapping) and isinstance(previous_models, Mapping)):
                continue
            for model_id in sorted(set(current_models) & set(previous_models)):
                current_cost = _as_dict(
                    _as_dict(current_models[model_id]).get("cost")
                )
                previous_cost = _as_dict(
                    _as_dict(previous_models[model_id]).get("cost")
                )
                if current_cost != previous_cost and current_cost:
                    report["priceChanges"].append(
                        {
                            "provider": str(provider),
                            "modelID": str(model_id),
                            "before": previous_cost,
                            "after": current_cost,
                        }
                    )
    return report


def build_plan(
    sources: Sequence[tuple[Path, str, str]],
    client: GraphQLClient,
    *,
    decisions_path: Path | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Build a safe, JSON-serializable plan from source files and AxonHub.

    ``sources`` is a list of ``(source_path, provider, channel)`` triples;
    each provider's models are planned against its own channel.
    """

    if decisions_path is None:
        raise SyncError("model decisions path is required")
    scope = load_managed_scope(decisions_path)
    validate_provider_channels_in_scope(
        {provider: channel for _, provider, channel in sources}, scope
    )
    provider_nodes: dict[str, dict[str, Any]] = {}
    provider_channel: dict[str, str] = {}
    source_files: list[Path] = []
    for source_path, provider, channel in sources:
        document = load_json(source_path)
        node = find_provider(document, provider)
        if provider in provider_nodes:
            raise SyncError(f"provider {provider!r} appears in multiple sources")
        provider_nodes[provider] = node
        provider_channel[provider] = channel
        source_files.append(source_path)

    provider_excluded: dict[str, dict[str, str]] = {}
    provider_supplements: dict[str, dict[str, dict[str, Any]]] = {}
    for provider in provider_nodes:
        decisions = load_model_decisions(decisions_path, provider)
        provider_excluded[provider] = decisions["excluded"]
        provider_supplements[provider] = decisions["supplements"]

    warnings: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    records: dict[str, dict[str, Any]] = {}
    record_provider: dict[str, str] = {}
    for provider, node in sorted(provider_nodes.items()):
        for model in _model_records(node):
            model_id = _model_id(model)
            if model_id in provider_excluded[provider]:
                reason = provider_excluded[provider][model_id]
                excluded.append(
                    {"modelID": model_id, "reason": "manual_exclude", "detail": reason}
                )
                continue
            if model_id in records:
                warnings.append(
                    {
                        "type": "duplicate_model_across_sources",
                        "model": model_id,
                        "kept": record_provider[model_id],
                        "dropped": provider,
                    }
                )
                continue
            records[model_id] = model
            record_provider[model_id] = provider
    if not records:
        raise SyncError("source providers have no models")

    remark_values = {model_id: _remark_values(model) for model_id, model in records.items()}
    for provider, supplements in sorted(provider_supplements.items()):
        for model_id, fields in sorted(supplements.items()):
            if model_id in remark_values:
                remark_values[model_id].update(fields)
            else:
                warnings.append({"type": "supplement_unknown_model", "model": model_id})
    for provider in sorted(provider_nodes):
        provider_records = {
            model_id: values
            for model_id, values in remark_values.items()
            if record_provider[model_id] == provider
        }
        fixed, derived = _fix_free_records(provider_records)
        for model_id, filled in derived.items():
            warnings.append(
                {
                    "type": "free_default_filled",
                    "model": model_id,
                    "fields": filled,
                    "provider": provider,
                }
            )
        remark_values.update(fixed)
    for model_id, values in sorted(remark_values.items()):
        missing = [field for field in REMARK_FIELDS if values.get(field) is None]
        if missing:
            warnings.append(
                {"type": "missing_remark_fields", "model": model_id, "fields": missing}
            )

    channels = fetch_channels(client, page_size)
    existing = fetch_models(client, page_size)
    errors: list[dict[str, Any]] = []
    decision_required: list[dict[str, Any]] = []
    channel_nodes: dict[str, dict[str, Any]] = {}
    for provider, channel in sorted(provider_channel.items()):
        node = channels.get(channel)
        if node is None:
            raise SyncError(f"target channel {channel!r} not found in AxonHub")
        if str(node.get("status", "")).lower() != "enabled":
            warnings.append(
                {"type": "disabled_channel", "channel": channel, "status": node.get("status")}
            )
        channel_nodes[channel] = node

    channel_updates: list[dict[str, Any]] = []
    for channel, node in sorted(channel_nodes.items()):
        channel_models = sorted(
            model_id
            for model_id, owner in record_provider.items()
            if provider_channel[owner] == channel
        )
        before = list(node.get("supportedModels") or [])
        default_test_model = node.get("defaultTestModel")
        if default_test_model and default_test_model not in channel_models and default_test_model not in before:
            errors.append(
                {
                    "type": "default_test_model_removed",
                    "channel": channel,
                    "model": default_test_model,
                }
            )
            continue
        if _append_only_channel(channel):
            missing = [m for m in channel_models if m not in before]
            if missing:
                channel_updates.append({
                    "channel": channel,
                    "channelId": str(node["id"]),
                    "before": before,
                    "after": sorted(set(before) | set(missing)),
                    "appended": sorted(missing),
                    "input": {"supportedModels": sorted(set(before) | set(missing))},
                })
        elif before != channel_models:
            channel_updates.append({
                "channel": channel,
                "channelId": str(node["id"]),
                "before": before,
                "after": channel_models,
                "input": {"supportedModels": channel_models},
            })

    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    deletes: list[dict[str, Any]] = []
    externally_retained: list[dict[str, Any]] = []
    blocking_references: list[dict[str, Any]] = []
    for item in excluded:
        model_id = str(item["modelID"])
        external_channels = sorted(
            name
            for name, other in channels.items()
            if name not in set(provider_channel.values())
            and model_id in (other.get("supportedModels") or [])
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
                    "reason": item.get("detail", "excluded"),
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
    for model_id in sorted(records):
        model = records[model_id]
        provider = record_provider[model_id]
        channel = provider_channel[provider]
        channel_id = int(str(channel_nodes[channel]["id"]).rsplit("/", 1)[-1])
        developer, icon, group = _model_meta(model, provider)
        new_remark = remark_json(build_remark(existing.get(model_id, {}).get("remark"), remark_values[model_id]))
        managed = {
            "developer": developer,
            "name": str(model.get("name") or model_id),
            "icon": icon,
            "group": group,
            "modelCard": model_card(model),
            "remark": new_remark,
            "settings": reconcile_candidate_settings(
                existing.get(model_id, {}).get("settings"),
                model_id=model_id,
                channel_id=channel_id,
            ),
        }
        old = existing.get(model_id)
        if old is None:
            creates.append({
                "modelID": model_id,
                "channel": channel,
                "input": {
                    "modelID": model_id,
                    "name": managed["name"],
                    "developer": developer,
                    "type": "chat",
                    "icon": icon,
                    "group": group,
                    "modelCard": managed["modelCard"],
                    "remark": new_remark,
                    "settings": managed["settings"],
                },
            })
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
                name: list(other.get("supportedModels") or [])
                for name, other in sorted(channels.items())
            },
            "associations": {
                model_id: (other.get("settings") or {}).get("associations") or []
                for model_id, other in sorted(existing.items())
            },
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": sorted(provider_nodes),
        "channel": sorted(channel_nodes),
        "sourceFingerprints": {
            "sources": {
                str(path): file_sha256(path)
                for path in sorted(set(source_files), key=lambda item: str(item))
            },
            "decisions": file_sha256(decisions_path),
        },
        "sourceCount": len(records),
        "enabledChannels": sorted(channel_nodes),
        "skippedChannels": [],
        "warnings": warnings,
        "decisionRequired": decision_required,
        "errors": errors,
        "included": sorted(records),
        "excluded": sorted(excluded, key=lambda item: item["modelID"]),
        "externallyRetained": externally_retained,
        "blockingReferences": blocking_references,
        "channelUpdates": channel_updates,
        "creates": creates,
        "updates": updates,
        "deletes": deletes,
        "deletionGuardFingerprint": deletion_guard,
    }


CREATE_MODEL_MUTATION_DOC = """
Reference only (documented in references/schema.md): execution uses these
mutations from the axonhub-admin skill — createModel, updateModel,
updateChannel (supportedModels only), deleteModel.  This planner never sends
them.
"""


def validate_plan_shape(plan: Mapping[str, Any]) -> None:
    """Reject malformed or over-broad saved plans before execution."""

    if plan.get("schema_version") != SCHEMA_VERSION:
        raise BlockingPlanError("unsupported sync plan schema_version")
    if not isinstance(plan.get("provider"), str) or not plan["provider"]:
        raise BlockingPlanError("sync plan has no provider")
    if not isinstance(plan.get("channel"), str) or not plan["channel"]:
        raise BlockingPlanError("sync plan has no channel")
    fingerprints = plan.get("sourceFingerprints")
    if not isinstance(fingerprints, Mapping) or not fingerprints.get("source"):
        raise BlockingPlanError("sync plan has no source fingerprints")
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


def validate_source_files(
    plan: Mapping[str, Any],
    source_path: Path,
    decisions_path: Path,
) -> None:
    expected = plan.get("sourceFingerprints")
    if not isinstance(expected, Mapping):
        raise BlockingPlanError("sync plan has no source fingerprints")
    actual_source = file_sha256(source_path)
    actual_decisions = file_sha256(decisions_path)
    if (
        expected.get("source") != actual_source
        or expected.get("decisions") != actual_decisions
    ):
        raise BlockingPlanError("sync sources changed after review; regenerate the plan")


def plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": plan.get("provider"),
        "channel": plan.get("channel"),
        "sourceCount": plan.get("sourceCount", 0),
        "enabledChannels": plan.get("enabledChannels", []),
        "warningCount": len(plan.get("warnings") or []),
        "excludedCount": len(plan.get("excluded") or []),
        "externallyRetainedCount": len(plan.get("externallyRetained") or []),
        "errorCount": len(plan.get("errors") or []),
        "createCount": len(plan.get("creates") or []),
        "updateCount": len(plan.get("updates") or []),
        "deleteCount": len(plan.get("deletes") or []),
        "channelUpdateCount": len(plan.get("channelUpdates") or []),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan provider catalogs from source JSONs for AxonHub (read-only)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        action="append",
        dest="sources",
        help="api.json-shaped source file; repeatable (e.g. data/goat-models.json data/opencode-go-models.json)",
    )
    parser.add_argument(
        "--provider-channel",
        action="append",
        dest="provider_channels",
        metavar="PROVIDER=CHANNEL",
        help="reaffirm a provider→channel routing; must match the managed scope "
        "in --model-decisions (default routing comes from that scope)",
    )
    parser.add_argument(
        "--model-decisions",
        type=Path,
        default=Path("config/model-decisions.json"),
    )
    parser.add_argument(
        "--all-models",
        type=Path,
        default=Path("data/all_models.json"),
        help="all_models snapshot used by the change report (added/removed models)",
    )
    parser.add_argument(
        "--change-report-output",
        type=Path,
        help="write the change report (added/removed models, price changes) here",
    )
    parser.add_argument("--axonhub-url", default=os.environ.get("AXONHUB_URL", "https://axon.jasonqin.site"))
    parser.add_argument(
        "--token",
        default=os.environ.get("AXONHUB_JWT"),
        help="read-only-capable JWT for querying AxonHub state (prefer AXONHUB_JWT; never print it)",
    )
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--page-size", type=int, default=100)
    return parser


def _resolve_provider(document: Any, source_path: Path, provider: str | None) -> str:
    if provider:
        return provider
    candidates = sorted(
        key
        for key, value in document.items()
        if isinstance(value, Mapping) and "models" in value
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SyncError(f"source {source_path} has no provider nodes")
    raise SyncError(
        f"source {source_path} has multiple providers {candidates}; pass --provider"
    )


def _parse_provider_channels(values: Sequence[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or ():
        provider, separator, channel = value.partition("=")
        if not separator or not provider.strip() or not channel.strip():
            raise SyncError(f"invalid --provider-channel {value!r}; use PROVIDER=CHANNEL")
        result[provider.strip()] = channel.strip()
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.sources:
        print("--source is required for planning", file=sys.stderr)
        return 2
    try:
        scope = load_managed_scope(args.model_decisions)
        provider_channels = _parse_provider_channels(args.provider_channels)
        validate_provider_channels_in_scope(provider_channels, scope)
        sources: list[tuple[Path, str, str]] = []
        for source_path in args.sources:
            document = load_json(source_path)
            provider = _resolve_provider(document, source_path, None)
            sources.append((source_path, provider, managed_channel(provider, scope)))
        if not args.token:
            print(
                "a JWT is required to read AxonHub state: set AXONHUB_JWT or pass --token",
                file=sys.stderr,
            )
            return 2
        if args.change_report_output:
            report = change_report(args.all_models, args.sources)
            write_json(args.change_report_output, report)
            print(
                json.dumps(
                    {
                        "addedModels": len(report["addedModels"]),
                        "removedModels": len(report["removedModels"]),
                        "priceChanges": len(report["priceChanges"]),
                    },
                    sort_keys=True,
                )
            )
        client = GraphQLClient(args.axonhub_url, args.token)
        plan = build_plan(
            sources,
            client,
            decisions_path=args.model_decisions,
            page_size=args.page_size,
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
        return 0
    except SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
