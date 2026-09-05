"""Unit tests for the confirmed request-mapping helper."""

import csv
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import apply_mapping  # noqa: E402


def write_mapping(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=apply_mapping.CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_decisions(path: Path) -> None:
    path.write_text(
        '{"schema_version":1,"scope":{'
        '"channels":["opencode-go","op-responses","op-anthropic"],'
        '"templates":["stable","claude","gpt"]},'
        '"models":[],"mapping_overrides":[]}',
        encoding="utf-8",
    )


def template_node(name: str, mappings: dict[str, str]) -> dict:
    return {
        "id": f"gid://axonhub/APIKeyProfileTemplate/{name}",
        "name": name,
        "description": "",
        "projectID": "gid://axonhub/Project/1",
        "linkedProfilesCount": 1,
        "profile": apply_mapping.profile_with_mappings(None, name, mappings),
    }


def model_node(
    model_id: str,
    associations: list[dict] | None = None,
    *,
    internal_id: str | None = None,
) -> dict:
    return {
        "id": internal_id or f"gid://axonhub/Model/{model_id}",
        "modelID": model_id,
        "status": "enabled",
        "settings": {
            "disableDeveloperSettingsInheritance": False,
            "associations": associations or [],
            "loadBalancerStrategy": "default",
            "traceStickyMode": "default",
        },
    }


def test_build_model_association_has_canonical_shape() -> None:
    assert apply_mapping.build_model_association("deepseek-v4-flash") == {
        "type": "model",
        "priority": 0,
        "disabled": False,
        "channelModel": None,
        "channelRegex": None,
        "regex": None,
        "modelId": {"modelId": "deepseek-v4-flash", "exclude": None},
        "channelTagsModel": None,
        "channelTagsRegex": None,
    }


def test_settings_replaces_only_associations() -> None:
    settings = {
        "disableDeveloperSettingsInheritance": True,
        "loadBalancerStrategy": "round_robin",
        "traceStickyMode": "prefer_previous_channel",
        "associations": [{"type": "channel_model"}],
    }

    updated = apply_mapping.settings_with_single_model_association(settings, "target")

    assert updated["disableDeveloperSettingsInheritance"] is True
    assert updated["loadBalancerStrategy"] == "round_robin"
    assert updated["traceStickyMode"] == "prefer_previous_channel"
    assert updated["associations"] == [apply_mapping.build_model_association("target")]
    assert settings["associations"] == [{"type": "channel_model"}]


def test_current_target_understands_model_and_legacy_channel_associations() -> None:
    assert apply_mapping.current_target(
        {"associations": [{"type": "model", "modelId": {"modelId": "target"}}]}
    ) == "target"
    assert apply_mapping.current_target(
        {
            "associations": [
                {
                    "type": "channel_model",
                    "channelModel": {"channelId": 7, "modelId": "target"},
                }
            ]
        }
    ) == "channel:7/target"


def test_read_mapping_rows_rejects_unknown_target(tmp_path: Path) -> None:
    path = tmp_path / "models.csv"
    write_mapping(
        path,
        [
            {"model_id": "candidate-a", "role": "candidate", "arena_score": "1500", "rp5h": "100"},
            {"model_id": "claude-sonnet", "role": "request", "arena_score": "1500", "rp5h": "", "mapping": "missing"},
        ],
    )

    with pytest.raises(apply_mapping.MappingInputError, match="unknown candidate"):
        apply_mapping.read_mapping_rows(path)


def test_fetch_all_models_builds_index_from_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_connection(
        url: str, token: str, field: str, node_selection: str, *, page_size: int = 100
    ) -> list[dict]:
        seen["field"] = field
        seen["page_size"] = page_size
        return [
            model_node("candidate-a"),
            model_node("candidate-b"),
            {"id": "gid://axonhub/Model/anonymous"},
        ]

    monkeypatch.setattr(apply_mapping, "fetch_connection", fake_connection)

    result = apply_mapping.fetch_all_models("https://axonhub", "token", page_size=50)

    assert set(result) == {"candidate-a", "candidate-b"}
    assert seen == {"field": "models", "page_size": 50}


def test_build_plan_reports_legacy_change_and_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "models.csv"
    request_path = tmp_path / "request-models.json"
    decisions_path = tmp_path / "model-decisions.json"
    write_decisions(decisions_path)
    request_path.write_text(
        '{"schema_version":1,"models":['
        '{"model_id":"claude-sonnet","enabled":true},'
        '{"model_id":"gpt-mini","enabled":true}]}',
        encoding="utf-8",
    )
    write_mapping(
        path,
        [
            {"model_id": "candidate-a", "role": "candidate", "arena_score": "1500", "rp5h": "100", "mapping": ""},
            {"model_id": "claude-sonnet", "role": "request", "arena_score": "1600", "rp5h": "100", "mapping": "candidate-a"},
            {"model_id": "gpt-mini", "role": "request", "arena_score": "1400", "rp5h": "80", "mapping": "candidate-a"},
        ],
    )
    remote = {
        "candidate-a": model_node("candidate-a"),
        "claude-sonnet": model_node(
            "claude-sonnet",
            [
                {
                    "type": "channel_model",
                    "disabled": False,
                    "channelModel": {"channelId": 7, "modelId": "candidate-a"},
                }
            ],
        ),
        "gpt-mini": model_node(
            "gpt-mini", [apply_mapping.build_model_association("candidate-a")]
        ),
    }
    monkeypatch.setattr(apply_mapping, "fetch_all_models", lambda *_args: remote)
    monkeypatch.setattr(
        apply_mapping,
        "fetch_managed_templates",
        lambda *_args: {
            "stable": template_node(
                "stable", {"claude-sonnet": "candidate-a", "gpt-mini": "candidate-a"}
            ),
            "claude": template_node("claude", {"claude-sonnet": "candidate-a"}),
        },
    )

    plan = apply_mapping.build_plan(
        path, request_path, decisions_path, "https://axonhub", "token"
    )

    assert [item["requestModel"] for item in plan["changes"]] == ["claude-sonnet"]
    assert [item["requestModel"] for item in plan["noops"]] == ["gpt-mini"]
    assert any(item["code"] == "legacy_channel_association" for item in plan["warnings"])


def test_apply_plan_sends_preserved_settings_only_for_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = {
        "disableDeveloperSettingsInheritance": True,
        "loadBalancerStrategy": "round_robin",
        "traceStickyMode": "default",
        "associations": [apply_mapping.build_model_association("target")],
    }
    plan = {
        "errors": [],
        "changes": [
            {
                "requestModel": "request",
                "targetModel": "target",
                "internalID": "gid://axonhub/Model/request",
                "settings": settings,
            }
        ],
    }
    calls: list[dict] = []

    def fake_fetch(url: str, token: str, query: str, variables: dict) -> dict:
        calls.append(variables)
        return {"updateModel": {"id": variables["id"], "modelID": "request"}}

    monkeypatch.setattr(apply_mapping, "fetch_graphql", fake_fetch)

    result = apply_mapping.apply_plan("https://axonhub", "token", plan)

    assert result == {
        "applied": ["request"],
        "appliedTemplates": [],
        "failures": [],
    }
    assert calls[0]["input"]["settings"]["disableDeveloperSettingsInheritance"] is True
    assert calls[0]["input"]["settings"]["associations"] == [
        apply_mapping.build_model_association("target")
    ]


def test_apply_plan_rejects_settings_drift_before_mutation() -> None:
    planned_settings = {
        "loadBalancerStrategy": "default",
        "associations": [{"type": "channel_model"}],
    }
    plan = {
        "errors": [],
        "changes": [
            {
                "requestModel": "request",
                "targetModel": "target",
                "internalID": "gid://axonhub/Model/request",
                "currentSettingsFingerprint": apply_mapping.settings_fingerprint(planned_settings),
                "settings": apply_mapping.settings_with_single_model_association(
                    planned_settings, "target"
                ),
            }
        ],
    }
    current_models = {
        "request": {
            "id": "gid://axonhub/Model/request",
            "modelID": "request",
            "settings": {**planned_settings, "loadBalancerStrategy": "round_robin"},
        }
    }

    with pytest.raises(apply_mapping.MappingInputError, match="stale"):
        apply_mapping.apply_plan("https://axonhub", "token", plan, current_models)


def test_apply_plan_rejects_noop_drift_before_mutation() -> None:
    planned = model_node(
        "request", [apply_mapping.build_model_association("target")]
    )
    plan = {
        "errors": [],
        "changes": [],
        "noops": [
            {
                "requestModel": "request",
                "targetModel": "target",
                "internalID": planned["id"],
                "currentSettingsFingerprint": apply_mapping.settings_fingerprint(
                    planned["settings"]
                ),
                "settings": planned["settings"],
            }
        ],
    }
    current = {
        "request": {
            **planned,
            "settings": {**planned["settings"], "traceStickyMode": "changed"},
        }
    }

    with pytest.raises(apply_mapping.MappingInputError, match="stale"):
        apply_mapping.apply_plan("https://axonhub", "token", plan, current)


def test_template_plan_preserves_manual_and_stable_is_union() -> None:
    existing = {
        "claude": template_node(
            "claude",
            {"claude-managed": "old", "claude-legacy": "manual-claude"},
        ),
        "gpt": template_node(
            "gpt", {"gpt-managed": "old", "gpt-5.3": "manual-gpt"}
        ),
        "stable": template_node(
            "stable", {"stable-only": "manual-stable"}
        ),
    }

    plan = apply_mapping.build_template_plan(
        {"claude-managed": "target-a", "gpt-managed": "target-b"},
        {"claude-managed", "gpt-managed"},
        {"stable", "claude", "gpt"},
        existing,
    )

    after = {
        item["name"]: item["afterMappings"]
        for item in plan["changes"] + plan["creates"] + plan["noops"]
    }
    assert after["claude"] == {
        "claude-legacy": "manual-claude",
        "claude-managed": "target-a",
    }
    assert after["gpt"] == {
        "gpt-5.3": "manual-gpt",
        "gpt-managed": "target-b",
    }
    assert after["stable"] == {
        "claude-legacy": "manual-claude",
        "claude-managed": "target-a",
        "gpt-5.3": "manual-gpt",
        "gpt-managed": "target-b",
        "stable-only": "manual-stable",
    }
    assert plan["warnings"][0]["code"] == "stable_only_manual_mappings"


def test_template_profile_compare_normalizes_null_collections() -> None:
    expected = apply_mapping.profile_with_mappings(
        None, "gpt", {"gpt-5": "target"}
    )
    actual = {
        **expected,
        "channelIDs": None,
        "channelTags": None,
        "modelIDs": None,
        "quota": None,
    }

    assert apply_mapping.normalize_profile_for_compare(
        actual
    ) == apply_mapping.normalize_profile_for_compare(expected)


def test_settings_fingerprint_treats_absent_settings_as_empty() -> None:
    assert apply_mapping.settings_fingerprint(None) == apply_mapping.settings_fingerprint({})
    assert apply_mapping.settings_fingerprint(None) == apply_mapping.value_fingerprint({})


def test_template_fingerprint_stable_for_missing_template() -> None:
    assert apply_mapping.template_fingerprint(None) == apply_mapping.value_fingerprint({})


def test_validate_saved_plan_checks_schema_and_hash_gates() -> None:
    with pytest.raises(apply_mapping.MappingInputError, match="schema_version"):
        apply_mapping.validate_saved_plan(
            {"schema_version": 2, "planHash": "x"}, set(), {}, set(), {}
        )

    plan = {"schema_version": 1}
    plan["planHash"] = apply_mapping.mapping_plan_hash(plan)
    with pytest.raises(apply_mapping.MappingInputError, match="must be a list"):
        apply_mapping.validate_saved_plan(plan, set(), {}, set(), {})
