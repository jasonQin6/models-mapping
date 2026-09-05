#!/usr/bin/env python3
"""Offline planner tests: CLI in (fixture snapshots + config), plan JSON out."""

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "sync_models.py"
SPEC = importlib.util.spec_from_file_location("sync_models", SCRIPT)
assert SPEC and SPEC.loader
sync_models = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_models)

DEFAULT_SCOPE = {
    "opencode-go": "opencode-go",
    "commandcode-goat": "commandcode",
}

FORBIDDEN_PLAN_KEYS = {
    "mode",
    "appended",
    "before",
    "beforeFingerprint",
    "sourceFingerprints",
    "deletionGuardFingerprint",
    "channelUpdates",
    "creates",
    "updates",
    "deletes",
    "decisionRequired",
}


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


def assert_plan_is_pure_desired_state(plan: dict) -> None:
    """The plan carries no mode, fingerprint, or remote before-state fields."""

    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert not (set(value) & FORBIDDEN_PLAN_KEYS), sorted(set(value))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(plan)


def run_main(
    tmp_path: Path,
    *sources: Path,
    decisions: Path | None = None,
    provider_channels: list[str] | None = None,
    plan_output: Path | None = None,
    change_report_output: Path | None = None,
) -> int:
    argv: list[str] = []
    for source in sources:
        argv += ["--source", str(source)]
    if decisions is not None:
        argv += ["--model-decisions", str(decisions)]
    for pair in provider_channels or []:
        argv += ["--provider-channel", pair]
    if plan_output is not None:
        argv += ["--plan-output", str(plan_output)]
    if change_report_output is not None:
        argv += ["--change-report-output", str(change_report_output)]
    return sync_models.main(argv)


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


def test_main_plans_exact_bare_id_lists_per_channel(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    goat = tmp_path / "goat.json"
    opengo = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(goat, raw_source("commandcode-goat", "model-a"))
    write_json(opengo, raw_source("opencode-go", "model-b", "model-c"))
    plan_output = tmp_path / "plan.json"

    rc = run_main(
        tmp_path, opengo, goat, decisions=decisions, plan_output=plan_output
    )

    assert rc == 0
    plan = json.loads(plan_output.read_text(encoding="utf-8"))
    assert_plan_is_pure_desired_state(plan)
    assert plan["schema_version"] == 2
    assert plan["providers"] == {
        "opencode-go": "opencode-go",
        "commandcode-goat": "commandcode",
    }
    assert plan["channels"]["commandcode"]["supportedModels"] == ["model-a"]
    assert plan["channels"]["opencode-go"]["supportedModels"] == ["model-b", "model-c"]
    entry = next(item for item in plan["models"] if item["modelID"] == "model-b")
    assert entry["channel"] == "opencode-go"
    assert entry["input"]["name"] == "model-b"
    assert entry["input"]["type"] == "chat"
    assert entry["input"]["modelCard"]["cost"] == {
        "input": 1,
        "output": 2,
        "cacheRead": 0.1,
        "cacheWrite": 0.2,
    }
    assert json.loads(entry["input"]["remark"]) == {
        "manual": "",
        "rp5h": None,
        "usage_quota": None,
        "context_threshold": None,
        "peak_hours": None,
        "retention": None,
    }
    summary = json.loads(capsys.readouterr().out)
    assert summary["channels"] == {"commandcode": 1, "opencode-go": 2}
    assert summary["modelCount"] == 3


def test_main_output_is_deterministic_byte_for_byte(tmp_path: Path) -> None:
    goat = tmp_path / "goat.json"
    opengo = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(goat, raw_source("commandcode-goat", "model-a", "free-x"))
    write_json(opengo, raw_source("opencode-go", "model-b"))
    first = tmp_path / "plan-1.json"
    second = tmp_path / "plan-2.json"

    assert run_main(tmp_path, opengo, goat, decisions=decisions, plan_output=first) == 0
    assert run_main(tmp_path, opengo, goat, decisions=decisions, plan_output=second) == 0

    assert first.read_bytes() == second.read_bytes()


def test_planner_has_no_network_or_jwt_surface() -> None:
    """Planning consumes snapshots + config only; no network imports, no token."""

    script_text = SCRIPT.read_text(encoding="utf-8")
    for banned in ("urllib", "http.client", "socket", "AXONHUB_JWT", "--token"):
        assert banned not in script_text


def test_main_lists_removal_candidates_with_execution_annotation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(
        tmp_path / "decisions.json",
        [
            {
                "provider": "p",
                "model_id": "expired",
                "action": "exclude",
                "reason": "dropped upstream",
            }
        ],
        scope={"p": "p"},
    )
    write_json(source, raw_source("p", "active", "expired"))
    plan_output = tmp_path / "plan.json"

    rc = run_main(tmp_path, source, decisions=decisions, plan_output=plan_output)

    assert rc == 0
    plan = json.loads(plan_output.read_text(encoding="utf-8"))
    assert plan["channels"]["p"]["supportedModels"] == ["active"]
    assert plan["removals"] == [
        {
            "modelID": "expired",
            "reason": "dropped upstream",
            "note": sync_models.REMOVAL_NOTE,
        }
    ]
    assert "执行时核验" in plan["removals"][0]["note"]


def test_main_duplicate_model_across_sources_keeps_first(
    tmp_path: Path,
) -> None:
    goat = tmp_path / "goat.json"
    opengo = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(goat, raw_source("commandcode-goat", "shared"))
    write_json(opengo, raw_source("opencode-go", "shared"))
    plan_output = tmp_path / "plan.json"

    rc = run_main(tmp_path, opengo, goat, decisions=decisions, plan_output=plan_output)

    assert rc == 0
    plan = json.loads(plan_output.read_text(encoding="utf-8"))
    assert [item["modelID"] for item in plan["models"]] == ["shared"]
    # 保留方由 sorted provider 顺序决定（commandcode-goat 在前），与 CLI 顺序无关
    assert plan["models"][0]["channel"] == "commandcode"
    assert plan["channels"]["opencode-go"]["supportedModels"] == []
    assert next(
        item
        for item in plan["warnings"]
        if item["type"] == "duplicate_model_across_sources"
    ) == {
        "type": "duplicate_model_across_sources",
        "model": "shared",
        "kept": "commandcode-goat",
        "dropped": "opencode-go",
    }


def test_main_fills_free_models_and_warns_missing_remark_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(tmp_path / "decisions.json", scope={"p": "p"})
    payload = raw_source("p", "paid-a", "ox-alpha-free", "plain")
    payload["p"]["models"]["paid-a"]["extra"] = {"rp5h": 900, "usage_quota": 60}
    write_json(source, payload)
    plan_output = tmp_path / "plan.json"

    rc = run_main(tmp_path, source, decisions=decisions, plan_output=plan_output)

    assert rc == 0
    plan = json.loads(plan_output.read_text(encoding="utf-8"))
    free_entry = next(
        item for item in plan["models"] if item["modelID"] == "ox-alpha-free"
    )
    assert json.loads(free_entry["input"]["remark"])["rp5h"] == 900.0
    assert json.loads(free_entry["input"]["remark"])["usage_quota"] == 60.0
    assert any(item["type"] == "free_default_filled" for item in plan["warnings"])
    assert any(
        item["type"] == "missing_remark_fields" and item["model"] == "plain"
        for item in plan["warnings"]
    )


def test_main_supplement_fills_missing_remark_fields(tmp_path: Path) -> None:
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
    plan_output = tmp_path / "plan.json"

    rc = run_main(tmp_path, source, decisions=decisions, plan_output=plan_output)

    assert rc == 0
    plan = json.loads(plan_output.read_text(encoding="utf-8"))
    entry = next(item for item in plan["models"] if item["modelID"] == "model-a")
    assert json.loads(entry["input"]["remark"])["rp5h"] == 900
    warned = next(
        item
        for item in plan["warnings"]
        if item["type"] == "missing_remark_fields" and item["model"] == "model-a"
    )
    assert "rp5h" not in warned["fields"]


def test_main_defaults_provider_channels_from_config(tmp_path: Path) -> None:
    goat = tmp_path / "goat.json"
    opengo = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(goat, raw_source("commandcode-goat", "model-a"))
    write_json(opengo, raw_source("opencode-go", "model-b"))
    plan_output = tmp_path / "plan.json"

    rc = run_main(tmp_path, opengo, goat, decisions=decisions, plan_output=plan_output)

    assert rc == 0
    plan = json.loads(plan_output.read_text(encoding="utf-8"))
    assert plan["providers"] == DEFAULT_SCOPE


def test_main_rejects_duplicate_provider_sources(tmp_path: Path) -> None:
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(first, raw_source("opencode-go", "model-a"))
    write_json(second, raw_source("opencode-go", "model-b"))

    rc = run_main(tmp_path, first, second, decisions=decisions)

    assert rc == 2


def test_build_plan_rejects_provider_outside_managed_scope(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("op-responses", "model-a"))

    with pytest.raises(sync_models.SyncError, match="not in the managed scope"):
        sync_models.build_plan(
            [(source, "op-responses", "op-responses")], decisions_path=decisions
        )


def test_build_plan_rejects_channel_outside_managed_scope(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("opencode-go", "model-a"))

    with pytest.raises(sync_models.SyncError, match="outside the managed scope"):
        sync_models.build_plan(
            [(source, "opencode-go", "commandcode")], decisions_path=decisions
        )


def test_build_plan_rejects_malformed_scope(tmp_path: Path) -> None:
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

    with pytest.raises(sync_models.SyncError, match="provider→channel mapping"):
        sync_models.build_plan(
            [(source, "opencode-go", "opencode-go")], decisions_path=decisions
        )


def test_main_rejects_provider_channel_outside_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "opengo.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("opencode-go", "model-a"))

    rc = run_main(
        tmp_path,
        source,
        decisions=decisions,
        provider_channels=["opencode-go=commandcode"],
    )

    assert rc == 2
    assert "outside the managed scope" in capsys.readouterr().err


def test_main_rejects_source_provider_outside_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "op-responses.json"
    decisions = decisions_file(tmp_path / "decisions.json")
    write_json(source, raw_source("op-responses", "model-a"))

    rc = run_main(tmp_path, source, decisions=decisions)

    assert rc == 2
    assert "not in the managed scope" in capsys.readouterr().err


def test_main_rejects_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "opengo.json"
    write_json(source, raw_source("opencode-go", "model-a"))

    rc = run_main(tmp_path, source, decisions=tmp_path / "missing.json")

    assert rc == 2
    assert "source file not found" in capsys.readouterr().err


def test_main_rejects_malformed_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
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

    rc = run_main(tmp_path, source, decisions=decisions)

    assert rc == 2
    assert "non-empty provider" in capsys.readouterr().err


def test_main_requires_source(capsys: pytest.CaptureFixture[str]) -> None:
    assert sync_models.main([]) == 2
    assert "--source is required" in capsys.readouterr().err


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
