#!/usr/bin/env python3
"""Unit tests for the OpenCode → AxonHub catalog synchronizer."""

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "sync_models.py"
SPEC = importlib.util.spec_from_file_location("sync_models", SCRIPT)
assert SPEC and SPEC.loader
sync_models = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_models)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def raw_cache(*model_ids: str) -> dict:
    return {
        "opencode-go": {
            "id": "opencode-go",
            "npm": "@ai-sdk/openai-compatible",
            "models": {
                model_id: {
                    "id": model_id,
                    "name": model_id,
                    "family": "test",
                    "cost": {
                        "input": 1,
                        "output": 2,
                        "cache_read": 0.1,
                        "cache_write": 0.2,
                    },
                }
                for model_id in model_ids
            },
        }
    }


def decisions_file(path: Path, models: list[dict] | None = None) -> Path:
    write_json(
        path,
        {
            "schema_version": 1,
            "scope": {
                "channels": ["opencode-go", "op-responses", "op-anthropic"],
                "templates": ["stable", "claude", "gpt"],
            },
            "models": models or [],
            "mapping_overrides": [],
        },
    )
    return path


def test_normalize_snapshot_replaces_only_requested_provider(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    existing = tmp_path / "models.json"
    write_json(cache, raw_cache("new-model"))
    write_json(
        existing,
        {
            "schema_version": 1,
            "providers": {"other": {"id": "other", "models": {}}},
        },
    )

    snapshot = sync_models.normalize_snapshot(cache, "opencode-go", existing)

    assert sorted(snapshot["providers"]) == ["opencode-go", "other"]
    assert list(snapshot["providers"]["opencode-go"]["models"]) == ["new-model"]


def test_model_card_reads_real_cache_cost_keys() -> None:
    model = raw_cache("model-a")["opencode-go"]["models"]["model-a"]

    assert sync_models.model_card(model)["cost"] == {
        "input": 1,
        "output": 2,
        "cacheRead": 0.1,
        "cacheWrite": 0.2,
    }


def test_remark_preserves_manual_and_clears_missing_fields() -> None:
    existing = json.dumps({"manual": "keep", "rp5h": 10, "retention": 30})

    result = sync_models.build_remark(existing, {"rp5h": 20})

    assert result == {
        "manual": "keep",
        "rp5h": 20,
        "usage_quota": None,
        "context_threshold": None,
        "peak_hours": None,
        "retention": None,
    }


def test_free_supplement_uses_maximum_quota() -> None:
    fixed = sync_models._fix_free_supplements(
        [{"id": "paid"}, {"id": "ox-alpha-free"}],
        {"paid": {"rp5h": 900, "usage_quota": 60}},
    )

    assert fixed["ox-alpha-free"] == {"rp5h": 900.0, "usage_quota": 60.0}


def test_build_plan_uses_cache_go_intersection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    go = tmp_path / "go.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(cache, raw_cache("cache-model"))
    write_json(
        go,
        {
            "schema_version": 1,
            "models": {
                "cache-model": {"rp5h": 100, "protocol": "completions"},
                "go-only": {"rp5h": 50, "protocol": "responses"},
            },
        },
    )
    monkeypatch.setattr(
        sync_models,
        "fetch_channels",
        lambda client, page_size: {
            "opencode-go": {
                "id": "gid://axonhub/Channel/6",
                "name": "opencode-go",
                "status": "enabled",
                "supportedModels": ["cache-model"],
                "defaultTestModel": "cache-model",
            },
            "op-responses": {
                "id": "gid://axonhub/Channel/7",
                "name": "op-responses",
                "status": "enabled",
                "supportedModels": ["go-only"],
                "defaultTestModel": "go-only",
            },
        },
    )
    monkeypatch.setattr(sync_models, "fetch_models", lambda client, page_size: {})

    plan = sync_models.build_plan(
        cache, "opencode-go", go, object(), decisions_path=decisions
    )

    assert {item["modelID"] for item in plan["creates"]} == {"cache-model"}
    assert plan["channelUpdates"] == []
    assert {item["modelID"] for item in plan["excluded"]} == {"go-only"}
    assert plan["errors"] == []


def test_paged_query_uses_axonhub_cursor_type() -> None:
    calls = []

    class Client:
        def execute(self, query, variables):
            calls.append((query, variables))
            return {
                "models": {
                    "edges": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }

    assert sync_models.paged_nodes(Client(), "models", "id modelID") == []
    assert "$after: Cursor" in calls[0][0]


def test_reviewed_plan_is_bound_to_source_files(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    go = tmp_path / "go.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(cache, raw_cache("model"))
    write_json(go, {"schema_version": 1, "models": {}})
    plan = {
        "sourceFingerprints": {
            "cache": sync_models.file_sha256(cache),
            "go": sync_models.file_sha256(go),
            "decisions": sync_models.file_sha256(decisions),
        }
    }

    sync_models.validate_source_files(plan, cache, go, decisions)
    write_json(go, {"schema_version": 1, "models": {"changed": {}}})

    with pytest.raises(sync_models.BlockingPlanError, match="sources changed"):
        sync_models.validate_source_files(plan, cache, go, decisions)


def test_go_protocol_overrides_provider_npm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    go = tmp_path / "go.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(cache, raw_cache("message-model"))
    write_json(
        go,
        {
            "schema_version": 1,
            "models": {"message-model": {"rp5h": 100, "protocol": "messages"}},
        },
    )
    monkeypatch.setattr(
        sync_models,
        "fetch_channels",
        lambda client, page_size: {
            "op-anthropic": {
                "id": "gid://axonhub/Channel/10",
                "name": "op-anthropic",
                "status": "enabled",
                "supportedModels": [],
                "defaultTestModel": None,
            }
        },
    )
    monkeypatch.setattr(sync_models, "fetch_models", lambda client, page_size: {})

    plan = sync_models.build_plan(
        cache, "opencode-go", go, object(), decisions_path=decisions
    )

    assert plan["errors"] == []
    assert plan["creates"][0]["channel"] == "op-anthropic"
    assert plan["channelUpdates"][0]["channel"] == "op-anthropic"


def test_manual_exclude_plans_unreferenced_existing_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    go = tmp_path / "go.json"
    decisions = decisions_file(
        tmp_path / "decisions.json",
        [
            {
                "provider": "opencode-go",
                "model_id": "expired",
                "action": "exclude",
                "reason": "expired",
            }
        ],
    )
    write_json(cache, raw_cache("active", "expired"))
    write_json(
        go,
        {
            "schema_version": 1,
            "models": {
                "active": {"rp5h": 100, "protocol": "completions"},
                "expired": {"rp5h": 100, "protocol": "completions"},
            },
        },
    )
    monkeypatch.setattr(
        sync_models,
        "fetch_channels",
        lambda client, page_size: {
            "opencode-go": {
                "id": "gid://axonhub/Channel/6",
                "name": "opencode-go",
                "status": "enabled",
                "supportedModels": ["active", "expired"],
                "defaultTestModel": "active",
            }
        },
    )
    expired = {
        "id": "gid://axonhub/Model/2",
        "modelID": "expired",
        "status": "enabled",
    }
    monkeypatch.setattr(
        sync_models,
        "fetch_models",
        lambda client, page_size: {"expired": expired},
    )

    plan = sync_models.build_plan(
        cache,
        "opencode-go",
        go,
        object(),
        decisions_path=decisions,
    )

    assert [item["modelID"] for item in plan["deletes"]] == ["expired"]
    assert plan["channelUpdates"][0]["after"] == ["active"]
    assert any(item["modelID"] == "expired" for item in plan["excluded"])


def test_candidate_association_moves_only_managed_channel_and_preserves_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    go = tmp_path / "go.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(cache, raw_cache("model"))
    write_json(
        go,
        {
            "schema_version": 1,
            "models": {"model": {"rp5h": 100, "protocol": "messages"}},
        },
    )
    channels = {
        "opencode-go": {
            "id": "gid://axonhub/Channel/6",
            "name": "opencode-go",
            "status": "enabled",
            "supportedModels": ["model"],
            "defaultTestModel": None,
        },
        "op-anthropic": {
            "id": "gid://axonhub/Channel/10",
            "name": "op-anthropic",
            "status": "enabled",
            "supportedModels": [],
            "defaultTestModel": None,
        },
        "external": {
            "id": "gid://axonhub/Channel/99",
            "name": "external",
            "status": "enabled",
            "supportedModels": ["model"],
            "defaultTestModel": None,
        },
    }
    existing = {
        "model": {
            "id": "gid://axonhub/Model/1",
            "modelID": "model",
            "name": "model",
            "developer": "opencode-go",
            "icon": "Default",
            "group": "test",
            "status": "enabled",
            "remark": "",
            "modelCard": sync_models.model_card(
                raw_cache("model")["opencode-go"]["models"]["model"]
            ),
            "settings": {
                "disableDeveloperSettingsInheritance": False,
                "associations": [
                    sync_models.candidate_channel_association(6, "model"),
                    sync_models.candidate_channel_association(99, "model"),
                ],
                "loadBalancerStrategy": "default",
                "traceStickyMode": "default",
            },
        }
    }
    monkeypatch.setattr(sync_models, "fetch_channels", lambda *_args: channels)
    monkeypatch.setattr(sync_models, "fetch_models", lambda *_args: existing)

    plan = sync_models.build_plan(
        cache, "opencode-go", go, object(), decisions_path=decisions
    )
    update = next(item for item in plan["updates"] if item["modelID"] == "model")
    associations = update["input"]["settings"]["associations"]

    assert [item["channelModel"]["channelId"] for item in associations] == [99, 10]


def test_cache_only_model_supported_externally_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache.json"
    go = tmp_path / "go.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(cache, raw_cache("active", "external-old"))
    write_json(
        go,
        {
            "schema_version": 1,
            "models": {"active": {"rp5h": 100, "protocol": "completions"}},
        },
    )
    channels = {
        "opencode-go": {
            "id": "gid://axonhub/Channel/6",
            "name": "opencode-go",
            "status": "enabled",
            "supportedModels": ["active"],
            "defaultTestModel": "active",
        },
        "external": {
            "id": "gid://axonhub/Channel/99",
            "name": "external",
            "status": "enabled",
            "supportedModels": ["external-old"],
            "defaultTestModel": "external-old",
        },
    }
    monkeypatch.setattr(sync_models, "fetch_channels", lambda *_args: channels)
    monkeypatch.setattr(sync_models, "fetch_models", lambda *_args: {})

    plan = sync_models.build_plan(
        cache, "opencode-go", go, object(), decisions_path=decisions
    )

    assert plan["deletes"] == []
    assert plan["externallyRetained"] == [
        {
            "modelID": "external-old",
            "externalChannels": ["external"],
            "references": [],
        }
    ]
