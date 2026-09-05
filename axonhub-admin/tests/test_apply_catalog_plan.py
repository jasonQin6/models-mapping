#!/usr/bin/env python3
"""Unit tests for the apply-side executor of catalog plans (axonhub-admin)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

SPEC = importlib.util.spec_from_file_location(
    "apply_catalog_plan", SCRIPT_DIR / "apply_catalog_plan.py"
)
assert SPEC and SPEC.loader
apply_catalog_plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apply_catalog_plan)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def channel_node(channel: str, channel_id: int, supported: list[str]) -> dict:
    return {
        "id": f"gid://axonhub/Channel/{channel_id}",
        "name": channel,
        "status": "enabled",
        "supportedModels": supported,
        "defaultTestModel": None,
    }


def sample_plan(**overrides: object) -> dict:
    plan = {
        "schema_version": 1,
        "provider": "p",
        "channel": "p",
        "sourceFingerprints": {
            "source": "deadbeef",
            "decisions": "cafef00d",
        },
        "sourceCount": 1,
        "enabledChannels": ["p"],
        "skippedChannels": [],
        "warnings": [],
        "decisionRequired": [],
        "errors": [],
        "included": ["new-model"],
        "excluded": [],
        "externallyRetained": [],
        "blockingReferences": [],
        "channelUpdates": [],
        "creates": [
            {
                "modelID": "new-model",
                "channel": "p",
                "input": {
                    "modelID": "new-model",
                    "name": "new-model",
                    "developer": "p",
                    "type": "chat",
                    "icon": "Default",
                    "group": "test",
                    "remark": "{}",
                    "settings": {
                        "disableDeveloperSettingsInheritance": False,
                        "associations": [],
                        "loadBalancerStrategy": "default",
                        "traceStickyMode": "default",
                    },
                },
            }
        ],
        "updates": [],
        "deletes": [],
        "deletionGuardFingerprint": "guard",
    }
    plan.update(overrides)
    return plan


def test_validate_plan_shape_accepts_sample_plan() -> None:
    apply_catalog_plan.validate_plan_shape(sample_plan())


def test_apply_plan_rejects_errors_in_plan() -> None:
    plan = sample_plan(errors=[{"type": "boom"}])

    with pytest.raises(
        apply_catalog_plan.BlockingPlanError, match="blocking errors"
    ):
        apply_catalog_plan.apply_plan("https://axonhub", "token", plan)


class _FakeHub:
    """Fake AxonHub: fetch_connection serves reads, fetch_graphql applies mutations."""

    def __init__(self, channels: dict, models: dict) -> None:
        self.channels = channels
        self.models = models

    def connection(
        self,
        axonhub_url: str,
        token: str,
        field: str,
        node_selection: str,
        page_size: int = 100,
    ) -> list[dict]:
        source = self.channels if field == "channels" else self.models
        return [dict(node) for node in source.values()]

    def graphql(
        self, axonhub_url: str, token: str, query: str, variables: dict | None = None
    ) -> dict:
        if "createModel" in query:
            variables = variables or {}
            input_data = dict(variables.get("input") or {})
            model_id = str(input_data.get("modelID"))
            self.models[model_id] = {
                "id": "gid://axonhub/Model/99",
                "modelID": model_id,
                "status": "disabled",
                "type": input_data.get("type"),
                "name": input_data.get("name"),
                "developer": input_data.get("developer"),
                "icon": input_data.get("icon"),
                "group": input_data.get("group"),
                "remark": input_data.get("remark"),
                "modelCard": input_data.get("modelCard"),
                "settings": input_data.get("settings"),
            }
            return {"createModel": {"id": "gid://axonhub/Model/99", "modelID": model_id}}
        if "updateModel" in query:
            variables = variables or {}
            model = self._by_internal_id(str(variables.get("id")))
            if model is not None:
                model.update(variables.get("input") or {})
            return {"updateModel": {"id": variables.get("id")}}
        if "deleteModel" in query:
            variables = variables or {}
            model = self._by_internal_id(str(variables.get("id")))
            if model is not None:
                self.models.pop(str(model["modelID"]))
            return {"deleteModel": None}
        if "updateChannel" in query:
            variables = variables or {}
            for node in self.channels.values():
                if str(node["id"]) == str(variables.get("id")):
                    node["supportedModels"] = (variables.get("input") or {}).get("supportedModels")
            return {"updateChannel": {}}
        raise AssertionError(f"unexpected query: {query[:60]}")

    def _by_internal_id(self, internal_id: str) -> dict | None:
        for node in self.models.values():
            if str(node.get("id")) == internal_id:
                return node
        return None


def _bind_hub(monkeypatch: pytest.MonkeyPatch, hub: _FakeHub) -> None:
    monkeypatch.setattr(apply_catalog_plan, "fetch_graphql", hub.graphql)
    monkeypatch.setattr(apply_catalog_plan, "fetch_connection", hub.connection)


def test_validate_remote_state_detects_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = sample_plan(
        updates=[
            {
                "modelID": "m",
                "internalID": "gid://axonhub/Model/1",
                "beforeFingerprint": "stale",
                "input": {"name": "changed"},
            }
        ]
    )
    hub = _FakeHub(
        {"p": channel_node("p", 6, [])},
        {
            "m": {
                "id": "gid://axonhub/Model/1",
                "modelID": "m",
                "status": "enabled",
            }
        },
    )
    _bind_hub(monkeypatch, hub)

    with pytest.raises(apply_catalog_plan.BlockingPlanError, match="changed after review"):
        apply_catalog_plan.validate_remote_state("https://axonhub", "token", plan)


def test_apply_plan_creates_enables_and_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = sample_plan(
        channelUpdates=[
            {
                "channel": "p",
                "channelId": "gid://axonhub/Channel/6",
                "before": [],
                "after": ["new-model"],
                "input": {"supportedModels": ["new-model"]},
            }
        ]
    )
    hub = _FakeHub({"p": channel_node("p", 6, [])}, {})
    _bind_hub(monkeypatch, hub)

    result = apply_catalog_plan.apply_plan("https://axonhub", "token", plan)

    assert result["created"] == ["new-model"]
    assert result["enabled"] == ["new-model"]
    assert result["updatedChannels"] == ["p"]
    assert hub.models["new-model"]["status"] == "enabled"
    assert hub.channels["p"]["supportedModels"] == ["new-model"]

    verification = apply_catalog_plan.verify_plan("https://axonhub", "token", plan)
    assert verification["ok"] is True


def test_verify_plan_detects_missing_channel_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = sample_plan(
        channelUpdates=[
            {
                "channel": "p",
                "channelId": "gid://axonhub/Channel/6",
                "before": [],
                "after": ["new-model"],
                "input": {"supportedModels": ["new-model"]},
            }
        ]
    )
    hub = _FakeHub({"p": channel_node("p", 6, [])}, {})
    _bind_hub(monkeypatch, hub)

    verification = apply_catalog_plan.verify_plan("https://axonhub", "token", plan)

    assert verification["ok"] is False
    assert {"type": "missing_model", "model": "new-model"} in verification["failures"]
    assert {"type": "channel_mismatch", "channel": "p"} in verification["failures"]


def test_validate_source_files_checks_fingerprints(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    write_json(source, {"p": {"models": {}}})
    plan = {
        "sourceFingerprints": {
            "source": apply_catalog_plan.file_sha256(source),
            "decisions": None,
        }
    }

    apply_catalog_plan.validate_source_files(plan, source, None)
    write_json(source, {"p": {"models": {"changed": {}}}})

    with pytest.raises(apply_catalog_plan.BlockingPlanError, match="source changed"):
        apply_catalog_plan.validate_source_files(plan, source, None)
