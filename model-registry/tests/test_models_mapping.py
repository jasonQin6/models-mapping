#!/usr/bin/env python3
"""Tests for the model-registry planner (dedupe, fill, cards, Claude mapping)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from models_mapping import (
    PlanningError,
    dedupe_registry,
    extract_series,
    main,
    plan_from,
    score_match,
    supersede_variants,
)

REPO = Path(__file__).resolve().parents[2]


def _rec(rp5h=None, quota=None, name="X", **kw):
    record = {"name": name, "rp5h": rp5h, "usage_quota": quota, "cost": {}}
    record.update(kw)
    return record


def _arena_doc(scores: dict[str, float]) -> dict:
    models = {
        model_id: {"arena_score": score, "arena_rank": index}
        for index, (model_id, score) in enumerate(sorted(scores.items()))
    }
    return {"schema_version": 1, "models": models}


def _decisions(channels=None, models=(), overrides=()):
    return {
        "channels": channels or {"opencode-go": "opencode-go", "commandcode-goat": "commandcode"},
        "excluded": {},
        "supplements": {},
        "overrides": {},
    }


def _plan(sections, cards=None, arena=None, requests=(), decisions=None):
    return plan_from(
        sections,
        cards=cards or {},
        arena_models={
            model_id: {"rating": entry["arena_score"], "rank": entry["arena_rank"]}
            for model_id, entry in _arena_doc(arena or {})["models"].items()
        },
        request_models=requests,
        decisions=decisions or _decisions(),
    )


def _request_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["model_id"]: row for row in rows if row["role"] == "request"}


def test_extract_series_only_matches_claude_gpt() -> None:
    assert extract_series("claude-haiku-4.5") == "claude"
    assert extract_series("gpt-5.5") == "gpt"
    assert extract_series("muse-spark-1.2") == ""


def test_dedupe_keeps_highest_rp5h_channel_and_reports() -> None:
    warnings: list[dict] = []
    registry = dedupe_registry(
        {
            "opencode-go": {"deepseek-v4-flash": _rec(rp5h=7600)},
            "commandcode-goat": {"deepseek-v4-flash": _rec(rp5h=18200)},
        },
        {},
        warnings,
    )

    entry = registry["deepseek-v4-flash"]
    assert entry["channel"] == "commandcode-goat"
    assert entry["record"]["rp5h"] == 18200
    assert entry["channel_aliases"] == {}
    assert warnings == [
        {"type": "duplicate_model_across_sources", "model": "deepseek-v4-flash",
         "kept": "commandcode-goat", "dropped": "opencode-go"}
    ]


def test_dedupe_null_loses_to_value_and_ties_keep_first_channel() -> None:
    warnings: list[dict] = []
    registry = dedupe_registry(
        {
            "opencode-go": {"minimax-m2.5": _rec(rp5h=None), "solo": _rec(rp5h=None)},
            "commandcode-goat": {"minimax-m2.5": _rec(rp5h=100), "solo": _rec(rp5h=None)},
        },
        {},
        warnings,
    )

    assert registry["minimax-m2.5"]["channel"] == "commandcode-goat"
    # Both null: the alphabetically first channel keeps the model.
    assert registry["solo"]["channel"] == "commandcode-goat"


def test_plan_moves_model_between_channels_in_supported_lists() -> None:
    sections = {
        "opencode-go": {"deepseek-v4-flash": _rec(rp5h=7600)},
        "commandcode-goat": {"deepseek-v4-flash": _rec(rp5h=18200), "kimi-k2.5": _rec(rp5h=900)},
    }

    _rows, plan = _plan(sections)

    assert plan["channels"]["opencode-go"]["supportedModels"] == []
    assert plan["channels"]["commandcode"]["supportedModels"] == ["deepseek-v4-flash", "kimi-k2.5"]
    assert plan["providers"] == {"opencode-go": "opencode-go", "commandcode-goat": "commandcode"}


def test_free_fill_recomputes_from_owning_channel() -> None:
    # The free model belongs to the goat channel, so its quotas must derive
    # from goat's non-free max (500), not from any other channel's values.
    sections = {
        "commandcode-goat": {"freebie": _rec(rp5h=None, quota=None), "paid": _rec(rp5h=500)},
    }

    _rows, plan = _plan(sections, arena={"paid": 1500.0})

    models = {m["modelID"]: m for m in plan["models"]}
    import json as _json
    remark = _json.loads(models["freebie"]["input"]["remark"])
    assert remark["rp5h"] == 500
    assert remark["usage_quota"] == 60
    assert any(w["type"] == "free_default_filled" and w["provider"] == "commandcode-goat" for w in plan["warnings"])


def test_claude_mapping_uses_formula_and_baseline_routing() -> None:
    sections = {
        "opencode-go": {
            "muse-spark-1.2": _rec(rp5h=800),      # arena 1200, close to haiku
            "qwen3.8-max": _rec(rp5h=100),          # arena 1600, far above
            "freebie": _rec(rp5h=500),              # free, arena 0 via free_default
        },
    }
    requests = [
        {"model_id": "claude-haiku-4.5", "arena_model_id": "claude-haiku-4-5", "enabled": True},
        {"model_id": "claude-opus-5", "enabled": True},
    ]
    arena = {"muse-spark-1.2": 1200.0, "qwen3.8-max": 1600.0, "claude-haiku-4-5": 1150.0, "claude-opus-5": 1700.0}

    rows, plan = _plan(sections, cards={}, arena=arena, requests=requests)

    request_rows = _request_rows(rows)
    # Baseline = lowest arena request (haiku): routes to the free candidate.
    assert request_rows["claude-haiku-4.5"]["mapping"] == "freebie"
    # Non-baseline uses the formula: closest score wins over the far-above one.
    assert request_rows["claude-opus-5"]["mapping"] == "qwen3.8-max"
    mapping_by_request = {m["request_model"]: m for m in plan["report"]["mappings"]}
    assert mapping_by_request["claude-haiku-4.5"]["match_confidence"] == "baseline"
    # Candidates are listed for review with their matched arena score.
    candidate_by_id = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    assert candidate_by_id["muse-spark-1.2"]["arena_score"] == "1200"
    # fill_free_records re-derived the free quota from the owning channel max.
    assert candidate_by_id["freebie"]["rp5h"] == "800"


def test_gpt_requests_are_pass_through() -> None:
    requests = [
        {"model_id": "claude-haiku-4.5", "enabled": True},
        {"model_id": "gpt-5.5", "enabled": True},
    ]
    sections = {"opencode-go": {"muse-spark-1.2": _rec(rp5h=800)}}

    rows, plan = _plan(sections, arena={"muse-spark-1.2": 1200.0, "claude-haiku-4.5": 1150.0}, requests=requests)

    request_rows = _request_rows(rows)
    assert set(request_rows) == {"claude-haiku-4.5"}
    assert all(m["request_model"] != "gpt-5.5" for m in plan["report"]["mappings"])


def test_mapping_override_wins_and_reports() -> None:
    decisions = _decisions()
    decisions["overrides"] = {"claude-opus-5": {"target_model": "muse-spark-1.2", "reason": "manual pick"}}
    requests = [{"model_id": "claude-opus-5", "enabled": True}]
    sections = {"opencode-go": {"muse-spark-1.2": _rec(rp5h=800), "qwen3.8-max": _rec(rp5h=900)}}

    rows, plan = _plan(
        sections,
        arena={"muse-spark-1.2": 1200.0, "qwen3.8-max": 1600.0, "claude-opus-5": 1700.0},
        requests=requests,
        decisions=decisions,
    )

    assert _request_rows(rows)["claude-opus-5"]["mapping"] == "muse-spark-1.2"
    assert plan["report"]["mappings"][0]["match_confidence"] == "override"


def test_channel_cost_wins_over_card_cost() -> None:
    cards = {"deepseek-v4-flash": {"name": "DeepSeek V4 Flash", "cost": {"input": 0.1, "output": 0.5}, "reasoning": True, "limit": {"context": 128000, "output": 8192}}}
    sections = {"commandcode-goat": {"deepseek-v4-flash": _rec(rp5h=18200, quota=60, cost={"input": 0.22, "output": 0.66})}}

    _rows, plan = _plan(sections, cards=cards)

    card = plan["models"][0]["input"]["modelCard"]
    assert card["cost"] == {"input": 0.22, "output": 0.66, "cacheRead": 0, "cacheWrite": 0}
    assert card["limit"] == {"context": 128000, "output": 8192}


def test_missing_card_warns_and_falls_back_to_channel_name() -> None:
    sections = {"commandcode-goat": {"goat-only-model": _rec(rp5h=100, name="Goat Only Model")}}

    _rows, plan = _plan(sections)

    assert any(w["type"] == "card_missing" and w["model"] == "goat-only-model" for w in plan["warnings"])
    assert plan["models"][0]["input"]["name"] == "Goat Only Model"


def test_exclude_removes_from_list_and_plans_removal() -> None:
    decisions = _decisions()
    decisions["excluded"] = {"commandcode-goat": {"minimax-m2.5": "Missing mapping-critical RP5H"}}
    sections = {"commandcode-goat": {"minimax-m2.5": _rec(rp5h=None), "kimi-k2.5": _rec(rp5h=900)}}

    _rows, plan = _plan(sections, decisions=decisions)

    assert plan["channels"]["commandcode"]["supportedModels"] == ["kimi-k2.5"]
    assert plan["removals"] == [
        {"modelID": "minimax-m2.5", "reason": "Missing mapping-critical RP5H", "note": plan["removals"][0]["note"]}
    ]


def test_unknown_channel_is_rejected() -> None:
    sections = {"some-new-channel": {"solo": _rec(rp5h=100)}}

    with pytest.raises(PlanningError):
        _plan(sections)


def test_request_without_arena_score_is_blocking() -> None:
    sections = {"opencode-go": {"muse-spark-1.2": _rec(rp5h=800)}}

    _rows, plan = _plan(sections, requests=[{"model_id": "claude-opus-5", "enabled": True}])

    assert any(e["code"] == "request_arena_missing" for e in plan["report"]["errors"])
    assert plan["report"]["counts"]["errors"] >= 1


def test_alias_merges_cross_channel_naming() -> None:
    aliases = {"tencent-hy3": "hy3"}
    sections = {
        "opencode-go": {"hy3": _rec(rp5h=4300)},
        "commandcode-goat": {"tencent-hy3": _rec(rp5h=7080)},
    }

    rows, plan = plan_from(
        sections,
        aliases=aliases,
        cards={},
        arena_models={},
        request_models=[],
        decisions=_decisions(),
    )

    # One canonical model, owned by the goat channel (7080 > 4300).
    models = {m["modelID"]: m for m in plan["models"]}
    assert set(models) == {"hy3"}
    assert models["hy3"]["channel"] == "commandcode"
    assert models["hy3"]["channelAliases"] == {"commandcode-goat": "tencent-hy3"}
    # Channel lists keep the native id each channel actually exposes.
    assert plan["channels"]["commandcode"]["supportedModels"] == ["tencent-hy3"]
    assert plan["channels"]["opencode-go"]["supportedModels"] == []


def test_collector_excluded_records_are_skipped() -> None:
    sections = {
        "commandcode-goat": {
            "glm-5.2-fast": _rec(rp5h=138, exclude="speed-variant (fast/highspeed)"),
            "kimi-k2.5": _rec(rp5h=None),
        },
    }

    _rows, plan = plan_from(sections, cards={}, arena_models={}, request_models=[], decisions=_decisions())

    assert all(m["modelID"] != "glm-5.2-fast" for m in plan["models"])
    assert plan["channels"]["commandcode"]["supportedModels"] == ["kimi-k2.5"]
    assert any(w["type"] == "collector_excluded" and w["model"] == "glm-5.2-fast" for w in plan["warnings"])


def test_free_variant_supersedes_plain_original() -> None:
    sections = {
        "opencode-go": {"longcat-2.0": _rec(rp5h=11400)},
        "commandcode-goat": {"longcat-2.0-free": _rec(rp5h=None)},
    }

    _rows, plan = plan_from(sections, cards={}, arena_models={}, request_models=[], decisions=_decisions())

    models = {m["modelID"]: m for m in plan["models"]}
    assert set(models) == {"longcat-2.0-free"}
    assert any(
        w["type"] == "variant_superseded" and w["model"] == "longcat-2.0" and w["replaced_by"] == "longcat-2.0-free"
        for w in plan["warnings"]
    )
    assert plan["channels"]["opencode-go"]["supportedModels"] == []
    assert plan["channels"]["commandcode"]["supportedModels"] == ["longcat-2.0-free"]


def test_contributor_variant_supersedes_plain_original() -> None:
    sections = {
        "commandcode-goat": {
            "muse-spark-1.2": _rec(rp5h=428),
            "muse-spark-1.2-contributor": _rec(rp5h=18200),
        },
    }

    _rows, plan = plan_from(sections, cards={}, arena_models={}, request_models=[], decisions=_decisions())

    models = {m["modelID"] for m in plan["models"]}
    assert models == {"muse-spark-1.2-contributor"}
    assert any(w["type"] == "variant_superseded" and w["model"] == "muse-spark-1.2" for w in plan["warnings"])


def test_rp5h_missing_low_arena_excluded_high_arena_review() -> None:
    sections = {
        "commandcode-goat": {
            "glm-5": _rec(rp5h=None),          # arena 1435 < 1500 -> excluded
            "kimi-k2.5": _rec(rp5h=None),      # no arena -> defaulted 1500 -> review
        },
    }
    arena = {"glm-5": 1435.72}

    _rows, plan = plan_from(sections, cards={}, arena_models=_arena_doc(arena)["models"], request_models=[], decisions=_decisions())

    models = {m["modelID"] for m in plan["models"]}
    assert models == {"kimi-k2.5"}
    assert any(w["type"] == "rp5h_missing_excluded" and w["model"] == "glm-5" for w in plan["warnings"])
    assert any(w["type"] == "rp5h_missing_review" and w["model"] == "kimi-k2.5" for w in plan["warnings"])
    assert any(w["type"] == "arena_defaulted" and w["model"] == "kimi-k2.5" for w in plan["warnings"])
    assert {"modelID": "kimi-k2.5", "reason": "missing_rp5h"} in plan["report"]["ineligible"]


def test_missing_arena_defaults_to_1500_and_enters_scoring() -> None:
    sections = {"opencode-go": {"omen-alpha": _rec(rp5h=11600)}}
    requests = [{"model_id": "claude-sonnet-5", "enabled": True}]
    arena = {"claude-sonnet-5": 1536.91, "muse-spark-1.3-contributor": 1622.48}
    sections["opencode-go"]["muse-spark-1.3-contributor"] = _rec(rp5h=45300)

    rows, plan = plan_from(
        sections,
        cards={},
        arena_models=_arena_doc(arena)["models"],
        request_models=requests,
        decisions=_decisions(),
    )

    assert any(w["type"] == "arena_defaulted" and w["model"] == "omen-alpha" for w in plan["warnings"])
    candidate_by_id = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    # The defaulted model participates in scoring (no hard-coded mapping).
    assert candidate_by_id["omen-alpha"]["arena_score"] == "1500"


def test_request_pinned_arena_score_skips_lookup() -> None:
    sections = {"opencode-go": {"omen-alpha": _rec(rp5h=11600)}}
    requests = [{"model_id": "claude-sonnet-5", "arena_score": 1536.91, "enabled": True}]

    rows, plan = plan_from(
        sections,
        cards={},
        arena_models={},
        request_models=requests,
        decisions=_decisions(),
    )

    request_rows = _request_rows(rows)
    assert request_rows["claude-sonnet-5"]["arena_score"] == "1536.91"
    assert request_rows["claude-sonnet-5"]["mapping"] == "omen-alpha"
    assert plan["report"]["errors"] == []


def test_score_formula_ignores_price_and_quota() -> None:
    candidate = {"model_id": "a", "arena_score": 1200.0, "rp5h": 800.0}
    priced = dict(candidate, cost={"input": 99.0}, usage_quota=1)

    assert score_match(1150.0, candidate, {"max_score": 1600.0, "max_rp5h": 1000.0, "max_score_diff": 500.0}) == (
        score_match(1150.0, priced, {"max_score": 1600.0, "max_rp5h": 1000.0, "max_score_diff": 500.0})
    )


def test_main_end_to_end_writes_csv_and_plan(tmp_path: Path) -> None:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "channels": {"opencode-go": {"muse-spark-1.2": _rec(rp5h=800)}},
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps(_arena_doc({"muse-spark-1.2": 1200.0, "claude-opus-5": 1700.0})), encoding="utf-8")
    requests = tmp_path / "request-models.json"
    requests.write_text(json.dumps({"schema_version": 1, "models": [{"model_id": "claude-opus-5", "enabled": True}]}), encoding="utf-8")
    decisions = tmp_path / "model-decisions.json"
    decisions.write_text(json.dumps({
        "schema_version": 1,
        "scope": {"channels": {"opencode-go": "opencode-go"}},
        "models": [],
        "mapping_overrides": [],
    }), encoding="utf-8")
    cards = tmp_path / "all_models.json"
    cards.write_text(json.dumps({}), encoding="utf-8")
    csv_out = tmp_path / "models.csv"
    plan_out = tmp_path / "plan.json"

    rc = main([
        "--extra", str(extra), "--cards", str(cards), "--arena", str(arena),
        "--request-models", str(requests), "--model-decisions", str(decisions),
        "--csv-output", str(csv_out), "--plan-output", str(plan_out),
    ])

    assert rc == 0
    assert "claude-opus-5,request" in csv_out.read_text(encoding="utf-8")
    plan = json.loads(plan_out.read_text(encoding="utf-8"))
    assert plan["schema_version"] == 2
    assert plan["channels"]["opencode-go"]["supportedModels"] == ["muse-spark-1.2"]


def test_main_fail_on_errors_exits_nonzero(tmp_path: Path) -> None:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({"schema_version": 1, "channels": {"opencode-go": {}}}), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps(_arena_doc({})), encoding="utf-8")
    requests = tmp_path / "request-models.json"
    requests.write_text(json.dumps({"schema_version": 1, "models": [{"model_id": "claude-opus-5", "enabled": True}]}), encoding="utf-8")
    decisions = tmp_path / "model-decisions.json"
    decisions.write_text(json.dumps({
        "schema_version": 1,
        "scope": {"channels": {"opencode-go": "opencode-go"}},
        "models": [],
        "mapping_overrides": [],
    }), encoding="utf-8")
    cards = tmp_path / "cards.json"
    cards.write_text("{}", encoding="utf-8")

    csv_out = tmp_path / "models.csv"

    rc = main([
        "--extra", str(extra), "--cards", str(cards), "--arena", str(arena),
        "--request-models", str(requests), "--model-decisions", str(decisions),
        "--csv-output", str(csv_out),
        "--fail-on-errors",
    ])

    assert rc == 1
