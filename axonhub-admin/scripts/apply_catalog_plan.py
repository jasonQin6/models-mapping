#!/usr/bin/env python3
"""Execute a reviewed models-mapping catalog plan against AxonHub.

The plan file is produced by ``models-mapping/scripts/sync_models.py``
and reviewed by a human.  This executor re-validates the plan shape, checks
every source fingerprint and remote before-state for drift, applies the
mutations, and reads back each written value.  It never regenerates a plan and
never performs Git operations.  GraphQL transport, pagination, and value
fingerprints are shared with the other executors via ``common.py``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from common import fetch_connection, fetch_graphql, value_fingerprint


SCHEMA_VERSION = 1


class SyncError(RuntimeError):
    """A user-actionable synchronization failure."""


class BlockingPlanError(SyncError):
    """Raised when a plan cannot safely be applied."""


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SyncError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"invalid JSON in {path}: {exc}") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def fetch_channels(axonhub_url: str, token: str, page_size: int = 100) -> dict[str, dict[str, Any]]:
    nodes = fetch_connection(axonhub_url, token, "channels", CHANNEL_NODE_SELECTION, page_size=page_size)
    return {str(node["name"]): node for node in nodes if node.get("name")}


def fetch_models(axonhub_url: str, token: str, page_size: int = 100) -> dict[str, dict[str, Any]]:
    nodes = fetch_connection(axonhub_url, token, "models", MODEL_NODE_SELECTION, page_size=page_size)
    result: dict[str, dict[str, Any]] = {}
    for node in nodes:
        model_id = node.get("modelID")
        if model_id:
            result[str(model_id)] = node
    return result


def validate_plan_shape(plan: Mapping[str, Any]) -> None:
    """Reject malformed or over-broad saved plans before any mutation."""

    if plan.get("schema_version") != SCHEMA_VERSION:
        raise BlockingPlanError("unsupported sync plan schema_version")
    provider = plan.get("provider")
    if isinstance(provider, list):
        if not provider or not all(isinstance(item, str) and item for item in provider):
            raise BlockingPlanError("sync plan has no provider")
    elif not isinstance(provider, str) or not provider:
        raise BlockingPlanError("sync plan has no provider")
    channel = plan.get("channel")
    if isinstance(channel, list):
        if not channel or not all(isinstance(item, str) and item for item in channel):
            raise BlockingPlanError("sync plan has no channel")
    elif not isinstance(channel, str) or not channel:
        raise BlockingPlanError("sync plan has no channel")
    fingerprints = plan.get("sourceFingerprints")
    if not isinstance(fingerprints, Mapping) or not (
        fingerprints.get("source") or fingerprints.get("sources")
    ):
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


def validate_remote_state(
    axonhub_url: str,
    token: str,
    plan: Mapping[str, Any],
    *,
    page_size: int = 100,
) -> None:
    """Ensure every reviewed before-state is still current."""

    current_models = fetch_models(axonhub_url, token, page_size)
    current_channels = fetch_channels(axonhub_url, token, page_size)
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
    source_path: Path | None,
    decisions_path: Path | None,
) -> None:
    expected = plan.get("sourceFingerprints")
    if not isinstance(expected, Mapping):
        raise BlockingPlanError("sync plan has no source fingerprints")
    sources = expected.get("sources")
    if isinstance(sources, Mapping):
        for key, recorded in sources.items():
            candidate = Path(key)
            if not candidate.exists() and source_path is not None and candidate.name == source_path.name:
                candidate = source_path
            if candidate.exists() and file_sha256(candidate) != recorded:
                raise BlockingPlanError(
                    "sync source changed after review; regenerate the plan"
                )
    elif source_path is not None and expected.get("source") is not None:
        if expected["source"] != file_sha256(source_path):
            raise BlockingPlanError(
                "sync source changed after review; regenerate the plan"
            )
    if decisions_path is not None:
        actual_decisions = file_sha256(decisions_path)
        if expected.get("decisions") != actual_decisions:
            raise BlockingPlanError(
                "model decisions changed after review; regenerate the plan"
            )


def apply_plan(
    axonhub_url: str,
    token: str,
    plan: Mapping[str, Any],
    *,
    page_size: int = 100,
) -> dict[str, Any]:
    """Apply a reviewed plan."""

    validate_plan_shape(plan)
    if plan.get("errors") or plan.get("decisionRequired"):
        raise BlockingPlanError("plan contains blocking errors; apply was refused")
    validate_remote_state(axonhub_url, token, plan, page_size=page_size)
    created: list[str] = []
    enabled: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []
    updated_channels: list[str] = []
    for item in plan.get("creates", []):
        response = fetch_graphql(axonhub_url, token, CREATE_MODEL_MUTATION, {"input": item["input"]})
        node = _as_dict(response.get("createModel"))
        internal_id = node.get("id")
        if not internal_id:
            raise SyncError(f"createModel returned no id for {item.get('modelID')}")
        model_id = str(item["modelID"])
        created.append(model_id)
        fetch_graphql(axonhub_url, token, UPDATE_MODEL_MUTATION, {"id": internal_id, "input": {"status": "enabled"}})
        enabled.append(model_id)
    for item in plan.get("updates", []):
        fetch_graphql(axonhub_url, token, UPDATE_MODEL_MUTATION, {"id": item["internalID"], "input": item["input"]})
        updated.append(str(item["modelID"]))
    for item in plan.get("channelUpdates", []):
        fetch_graphql(axonhub_url, token, UPDATE_CHANNEL_MUTATION, {"id": item["channelId"], "input": item["input"]})
        updated_channels.append(str(item["channel"]))
    for item in plan.get("deletes", []):
        fetch_graphql(axonhub_url, token, DELETE_MODEL_MUTATION, {"id": item["internalID"]})
        deleted.append(str(item["modelID"]))
    return {
        "created": created,
        "enabled": enabled,
        "updated": updated,
        "deleted": deleted,
        "updatedChannels": updated_channels,
    }


def verify_plan(axonhub_url: str, token: str, plan: Mapping[str, Any], *, page_size: int = 100) -> dict[str, Any]:
    """Verify desired model/channel values after apply."""

    actual_models = fetch_models(axonhub_url, token, page_size)
    actual_channels = fetch_channels(axonhub_url, token, page_size)
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


def plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": plan.get("provider"),
        "channel": plan.get("channel"),
        "sourceCount": plan.get("sourceCount", 0),
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
        description="Apply a reviewed models-mapping catalog plan to AxonHub",
    )
    parser.add_argument(
        "--plan-input",
        type=Path,
        required=True,
        help="reviewed plan JSON from models-mapping sync_models.py",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="original source file; re-checked against the plan fingerprint when given",
    )
    parser.add_argument(
        "--model-decisions",
        type=Path,
        default=Path("config/model-decisions.json"),
        help="decisions file; re-checked against the plan fingerprint when given",
    )
    parser.add_argument("--axonhub-url", default=os.environ.get("AXONHUB_URL", "https://axon.jasonqin.site"))
    parser.add_argument(
        "--token",
        default=os.environ.get("AXONHUB_JWT"),
        help="AxonHub JWT (prefer AXONHUB_JWT; never print it)",
    )
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="execute the mutations after validation; without it only validate and summarize",
    )
    parser.add_argument("--verify", action="store_true", help="read back all written values after apply")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.token:
        print(
            "a JWT is required: set AXONHUB_JWT or pass --token",
            file=sys.stderr,
        )
        return 2
    if args.verify and not args.apply:
        print("--verify requires --apply", file=sys.stderr)
        return 2
    try:
        plan = load_json(args.plan_input)
        if not isinstance(plan, Mapping):
            raise SyncError("plan input must be a JSON object")
        validate_plan_shape(plan)
        validate_source_files(plan, args.source, args.model_decisions)
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
        if not args.apply:
            print("plan validated; pass --apply after explicit user confirmation", file=sys.stderr)
            return 0
        result = apply_plan(args.axonhub_url, args.token, plan, page_size=args.page_size)
        print(json.dumps({"apply": result}, ensure_ascii=False, indent=2, sort_keys=True))
        if not args.verify:
            return 0
        verification = verify_plan(args.axonhub_url, args.token, plan, page_size=args.page_size)
        print(json.dumps({"verify": verification}, ensure_ascii=False, indent=2, sort_keys=True))
        if not verification["ok"]:
            return 3
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
