#!/usr/bin/env python3
"""Unit tests for the source-snapshot → AxonHub catalog planner."""

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


def raw_source(provider: str, *model_ids: str) -> dict:
    return {
        provider: {
            "id": provider,
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


DEFAULT_SCOPE = {
    "opencode-go": "opencode-go",
    "commandcode-goat": "commandcode",
}


def decisions_file(
    path: Path,
    models: list[dict] | None = None,
    scope: dict[str, str] | None = None,
) -> Path:
    write_json(
        path,
        {
            "schema_version": 1,
            "scope": {
                "channels": scope or DEFAULT_SCOPE,
                "templates": ["stable", "claude", "gpt"],
            },
            "models": models or [],
            "mapping_overrides": [],
        },
    )
    return path


def channel_node(channel: str, channel_id: int, supported: list[str]) -> dict:
    return {
        "id": f"gid://axonhub/Channel/{channel_id}",
        "name": channel,
        "status": "enabled",
        "supportedModels": supported,
        "defaultTestModel": None,
    }


def build_plan(sources: list[tuple[Path, str, str]], decisions: Path, monkeypatch, channels: dict, models: dict = None):
    monkeypatch.setattr(sync_models, "fetch_channels", lambda client, page_size: channels)
    monkeypatch.setattr(sync_models, "fetch_models", lambda client, page_size: models or {})
    return sync_models.build_plan(
        sources,
        object(),
        decisions_path=decisions,
    )


def test_model_card_reads_real_source_cost_keys() -> None:
    model = raw_source("p", "model-a")["p"]["models"]["model-a"]

    assert sync_models.model_card(model)["cost"] == {
        "input": 1,
        "output": 2,
        "cacheRead": 0.1,
        "cacheWrite": 0.2,
    }


def test_remark_values_read_extra_then_top_level() -> None:
    from_extra = sync_models._remark_values(
        {"extra": {"rp5h": 100, "usage_quota": 5}}
    )
    assert from_extra["rp5h"] == 100
    assert from_extra["usage_quota"] == 5
    assert from_extra["context_threshold"] is None

    from_top = sync_models._remark_values({"rp5h": 7, "peakHours": "09:00"})
    assert from_top["rp5h"] == 7
    assert from_top["peak_hours"] == "09:00"


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


def test_fix_free_records_fills_channel_maxima() -> None:
    records = {
        "paid-a": {"rp5h": 900, "usage_quota": 60},
        "paid-b": {"rp5h": 400, "usage_quota": 30},
        "some-free": {"rp5h": None, "usage_quota": None},
    }

    fixed, derived = sync_models._fix_free_records(records)

    assert fixed["some-free"] == {"rp5h": 900.0, "usage_quota": 60.0}
    assert derived == {"some-free": ["rp5h", "usage_quota"]}
    assert "paid-a" not in derived


def test_build_plan_includes_union_of_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    goat = tmp_path / "goat.json"
    opengo = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(goat, raw_source("commandcode-goat", "model-a"))
    write_json(opengo, raw_source("opencode-go", "model-b"))
    channels = {
        "commandcode": channel_node("commandcode", 20, []),
        "opencode-go": channel_node("opencode-go", 6, []),
    }

    plan = build_plan(
        [(opengo, "opencode-go", "opencode-go"), (goat, "commandcode-goat", "commandcode")],
        decisions,
        monkeypatch,
        channels,
    )

    assert plan["errors"] == []
    assert set(plan["included"]) == {"model-a", "model-b"}
    assert all(
        item["type"] in ("missing_remark_fields", "duplicate_model_across_sources")
        for item in plan["warnings"]
    )


def test_duplicate_model_across_sources_keeps_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    goat = tmp_path / "goat.json"
    opengo = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(goat, raw_source("commandcode-goat", "shared"))
    write_json(opengo, raw_source("opencode-go", "shared"))
    channels = {
        "commandcode": channel_node("commandcode", 20, []),
        "opencode-go": channel_node("opencode-go", 6, []),
    }

    plan = build_plan(
        [(opengo, "opencode-go", "opencode-go"), (goat, "commandcode-goat", "commandcode")],
        decisions,
        monkeypatch,
        channels,
    )

    assert plan["errors"] == []
    assert any(
        item["type"] == "duplicate_model_across_sources" for item in plan["warnings"]
    )
    assert plan["included"] == ["shared"]


def test_append_only_channel_only_adds_missing_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "goat.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("commandcode-goat", "new-model"))
    channels = {
        "commandcode": channel_node(
            "commandcode", 20, ["deepseek/deepseek-v4-flash", "gpt-5.6-sol"]
        )
    }

    plan = build_plan(
        [(source, "commandcode-goat", "commandcode")],
        decisions,
        monkeypatch,
        channels,
    )

    assert plan["errors"] == []
    update = plan["channelUpdates"][0]
    assert update["before"] == ["deepseek/deepseek-v4-flash", "gpt-5.6-sol"]
    assert "new-model" in update["appended"]
    assert "deepseek/deepseek-v4-flash" in update["after"]


def test_regular_channel_replaces_supported_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("opencode-go", "model-a", "model-b"))
    channels = {"opencode-go": channel_node("opencode-go", 6, ["model-a", "stale"])}

    plan = build_plan(
        [(source, "opencode-go", "opencode-go")],
        decisions,
        monkeypatch,
        channels,
    )

    assert plan["channelUpdates"][0]["after"] == ["model-a", "model-b"]
    assert "stale" not in plan["channelUpdates"][0]["after"]


def test_missing_rp5h_is_warning_not_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    payload = raw_source("p", "model-a")
    payload["p"]["models"]["model-a"]["extra"] = {"rp5h": None, "note": "x"}
    write_json(source, payload)
    channels = {"p": channel_node("p", 6, [])}

    plan = build_plan(
        [(source, "p", "p")],
        decisions_file(tmp_path / "decisions.json", scope={"p": "p"}),
        monkeypatch,
        channels,
    )

    assert plan["errors"] == []
    assert plan["decisionRequired"] == []
    assert any(
        item["type"] == "missing_remark_fields" and "rp5h" in item["fields"]
        for item in plan["warnings"]
    )
    create = plan["creates"][0]
    assert json.loads(create["input"]["remark"])["rp5h"] is None


def test_free_model_rp5h_filled_from_channel_maximum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    payload = raw_source("p", "paid-a", "ox-alpha-free")
    payload["p"]["models"]["paid-a"]["extra"] = {"rp5h": 900, "usage_quota": 60}
    write_json(source, payload)
    channels = {"p": channel_node("p", 6, [])}

    plan = build_plan(
        [(source, "p", "p")],
        decisions_file(tmp_path / "decisions.json", scope={"p": "p"}),
        monkeypatch,
        channels,
    )

    assert plan["errors"] == []
    free_create = next(c for c in plan["creates"] if c["modelID"] == "ox-alpha-free")
    assert json.loads(free_create["input"]["remark"])["rp5h"] == 900.0
    assert any(item["type"] == "free_default_filled" for item in plan["warnings"])


def test_supplement_fills_missing_remark_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(
        tmp_path / "decisions.json",
        [
            {
                "provider": "p",
                "model_id": "model-a",
                "action": "supplement",
                "reason": "documented quota",
                "fields": {"rp5h": 900},
            }
        ],
        scope={"p": "p"},
    )
    write_json(source, raw_source("p", "model-a"))
    channels = {"p": channel_node("p", 6, [])}

    plan = build_plan([(source, "p", "p")], decisions, monkeypatch, channels)

    assert plan["errors"] == []
    warned = next(
        item
        for item in plan["warnings"]
        if item["type"] == "missing_remark_fields" and item["model"] == "model-a"
    )
    assert "rp5h" not in warned["fields"]
    assert json.loads(plan["creates"][0]["input"]["remark"])["rp5h"] == 900


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


def test_manual_exclude_plans_unreferenced_existing_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(
        tmp_path / "decisions.json",
        [
            {
                "provider": "p",
                "model_id": "expired",
                "action": "exclude",
                "reason": "expired",
            }
        ],
        scope={"p": "p"},
    )
    write_json(source, raw_source("p", "active", "expired"))
    channels = {"p": channel_node("p", 6, ["active", "expired"])}
    expired = {
        "id": "gid://axonhub/Model/2",
        "modelID": "expired",
        "status": "enabled",
    }

    plan = build_plan(
        [(source, "p", "p")], decisions, monkeypatch, channels, {"expired": expired}
    )

    assert [item["modelID"] for item in plan["deletes"]] == ["expired"]
    assert plan["channelUpdates"][0]["after"] == ["active"]
    assert any(item["modelID"] == "expired" for item in plan["excluded"])


def test_candidate_association_moves_only_target_channel_and_preserves_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(tmp_path / "decisions.json", scope={"p": "p"})
    write_json(source, raw_source("p", "model"))
    channels = {
        "p": channel_node("p", 6, ["model"]),
        "external": channel_node("external", 99, ["model"]),
    }
    existing = {
        "model": {
            "id": "gid://axonhub/Model/1",
            "modelID": "model",
            "name": "model",
            "developer": "p",
            "icon": "Default",
            "group": "test",
            "status": "enabled",
            "remark": "",
            "modelCard": sync_models.model_card(
                raw_source("p", "model")["p"]["models"]["model"]
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

    plan = build_plan(
        [(source, "p", "p")], decisions, monkeypatch, channels, existing
    )
    update = next(item for item in plan["updates"] if item["modelID"] == "model")
    associations = update["input"]["settings"]["associations"]

    assert [item["channelModel"]["channelId"] for item in associations] == [99, 6]


def test_excluded_model_supported_externally_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(
        tmp_path / "decisions.json",
        [
            {
                "provider": "p",
                "model_id": "external-old",
                "action": "exclude",
                "reason": "dropped upstream",
            }
        ],
        scope={"p": "p"},
    )
    write_json(source, raw_source("p", "active"))
    # excluded 模型必须仍在源里才会进入 excluded 列表并被分析
    write_json(
        source,
        {
            "p": {
                "id": "p",
                "models": {
                    "active": raw_source("p", "active")["p"]["models"]["active"],
                    "external-old": raw_source("p", "x")["p"]["models"]["x"]
                    | {"id": "external-old"},
                },
            }
        },
    )
    channels = {
        "p": channel_node("p", 6, ["active"]),
        "external": channel_node("external", 99, ["external-old"]),
    }

    plan = build_plan([(source, "p", "p")], decisions, monkeypatch, channels)

    assert plan["deletes"] == []
    assert plan["externallyRetained"] == [
        {
            "modelID": "external-old",
            "externalChannels": ["external"],
            "references": [],
        }
    ]


def test_change_report_diffs_previous_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    all_models = tmp_path / "all_models.json"
    snapshot = tmp_path / "goat.json"
    write_json(all_models, {"new/model": {"id": "model"}, "kept/model": {"id": "kept"}})
    write_json(
        snapshot,
        {
            "commandcode-goat": {
                "models": {
                    "m1": {"cost": {"input": 1, "output": 2}},
                    "m2": {"cost": {"input": 1, "output": 2}},
                }
            }
        },
    )
    monkeypatch.setattr(
        sync_models,
        "_git_previous_blob",
        lambda path: json.dumps(
            {"old/model": {"id": "old"}, "kept/model": {"id": "kept"}}
        )
        if path == all_models
        else json.dumps(
            {
                "commandcode-goat": {
                    "models": {
                        "m1": {"cost": {"input": 1, "output": 3}},
                        "m2": {"cost": {"input": 1, "output": 2}},
                    }
                }
            }
        ),
    )

    report = sync_models.change_report(all_models, [snapshot])

    assert report["addedModels"] == ["new/model"]
    assert report["removedModels"] == ["old/model"]
    assert report["priceChanges"] == [
        {
            "provider": "commandcode-goat",
            "modelID": "m1",
            "before": {"input": 1, "output": 3},
            "after": {"input": 1, "output": 2},
        }
    ]


def test_build_plan_rejects_provider_outside_managed_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("op-responses", "model-a"))
    channels = {"op-responses": channel_node("op-responses", 7, [])}

    with pytest.raises(sync_models.SyncError, match="not in the managed scope"):
        build_plan([(source, "op-responses", "op-responses")], decisions, monkeypatch, channels)


def test_build_plan_rejects_channel_outside_managed_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("opencode-go", "model-a"))
    channels = {"commandcode": channel_node("commandcode", 20, [])}

    with pytest.raises(sync_models.SyncError, match="outside the managed scope"):
        build_plan([(source, "opencode-go", "commandcode")], decisions, monkeypatch, channels)


def test_build_plan_rejects_malformed_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    # 旧形状：channels 是字符串清单而非 provider→channel 映射
    write_json(
        decisions,
        {
            "schema_version": 1,
            "scope": {
                "channels": ["opencode-go", "op-responses"],
                "templates": ["stable", "claude", "gpt"],
            },
            "models": [],
            "mapping_overrides": [],
        },
    )
    write_json(source, raw_source("opencode-go", "model-a"))
    channels = {"opencode-go": channel_node("opencode-go", 6, [])}

    with pytest.raises(sync_models.SyncError, match="provider→channel mapping"):
        build_plan([(source, "opencode-go", "opencode-go")], decisions, monkeypatch, channels)


def test_main_defaults_provider_channels_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    goat = tmp_path / "goat.json"
    opengo = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(goat, raw_source("commandcode-goat", "model-a"))
    write_json(opengo, raw_source("opencode-go", "model-b"))
    captured: dict = {}

    def fake_build_plan(sources, client, *, decisions_path, page_size):
        captured["sources"] = sources
        captured["decisions_path"] = decisions_path
        return {"errors": [], "decisionRequired": []}

    monkeypatch.setattr(sync_models, "build_plan", fake_build_plan)

    rc = sync_models.main(
        [
            "--source",
            str(opengo),
            "--source",
            str(goat),
            "--model-decisions",
            str(decisions),
            "--token",
            "test-jwt",
        ]
    )

    assert rc == 0
    assert captured["sources"] == [
        (opengo, "opencode-go", "opencode-go"),
        (goat, "commandcode-goat", "commandcode"),
    ]
    assert captured["decisions_path"] == decisions


def test_main_rejects_provider_channel_outside_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("opencode-go", "model-a"))

    rc = sync_models.main(
        [
            "--source",
            str(source),
            "--model-decisions",
            str(decisions),
            "--provider-channel",
            "opencode-go=commandcode",
        ]
    )

    assert rc == 2
    assert "outside the managed scope" in capsys.readouterr().err


def test_main_rejects_source_provider_outside_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "op-responses.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("op-responses", "model-a"))

    rc = sync_models.main(
        ["--source", str(source), "--model-decisions", str(decisions)]
    )

    assert rc == 2
    assert "not in the managed scope" in capsys.readouterr().err


def test_main_rejects_missing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "opengo.json"
    write_json(source, raw_source("opencode-go", "model-a"))

    rc = sync_models.main(
        [
            "--source",
            str(source),
            "--model-decisions",
            str(tmp_path / "missing.json"),
        ]
    )

    assert rc == 2
    assert "source file not found" in capsys.readouterr().err


def test_main_rejects_malformed_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "opengo.json"
    write_json(source, raw_source("opencode-go", "model-a"))
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(
        decisions,
        {
            "schema_version": 1,
            "scope": {
                "channels": {"opencode-go": 6},
                "templates": ["stable", "claude", "gpt"],
            },
            "models": [],
            "mapping_overrides": [],
        },
    )

    rc = sync_models.main(
        ["--source", str(source), "--model-decisions", str(decisions)]
    )

    assert rc == 2
    assert "non-empty provider" in capsys.readouterr().err
