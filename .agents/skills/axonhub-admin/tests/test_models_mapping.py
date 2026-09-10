#!/usr/bin/env python3
"""Tests for the offline planner (axonhub-admin): dedupe, fill, cards, Claude mapping."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from models_mapping import (  # noqa: E402
    PlanningError,
    dedupe_registry,
    extract_series,
    load_arena,
    load_csv_requests,
    main,
    plan_from,
    score_match,
    supersede_variants,
)


def _rec(rp5h=None, quota=None, name="X", **kw):
    record = {"name": name, "rp5h": rp5h, "usage_quota": quota, "cost": {}}
    record.update(kw)
    return record


def _arena_doc(scores: dict[str, float]) -> dict:
    models = {model_id: {"arena_score": score} for model_id, score in scores.items()}
    return {"schema_version": 1, "models": models}


def _arena_models(arena: dict[str, float]) -> dict:
    return {model_id: {"rating": score} for model_id, score in arena.items()}


def _plan(sections, cards=None, arena=None, requests=(), aliases=None):
    # Aggregate view over the two plans for legacy assertions: channels from
    # the channel plan, models from the model plan, and the full run-level
    # warnings/report (artifact warning routing has its own test below).
    # Test cards are keyed by bare id, so refs map bare -> bare.
    rows, channel_plan, model_plan, report = plan_from(
        sections,
        aliases=aliases,
        cards=cards or {},
        card_refs={key: key for key in (cards or {})},
        arena_models=_arena_models(arena or {}),
        requests=requests,
    )
    merged = {
        "channels": channel_plan["channels"],
        "models": model_plan["models"],
        "warnings": report["warnings"],
        "report": report,
    }
    return rows, merged


def _request_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["model_id"]: row for row in rows if row["role"] == "request"}


def test_extract_series_only_matches_claude_gpt() -> None:
    assert extract_series("claude-haiku-4-5") == "claude"
    assert extract_series("gpt-5.5") == "gpt"
    assert extract_series("muse-spark-1.2") == ""


def test_load_csv_requests_reads_claude_rows_and_ignores_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "models.csv"
    path.write_text(
        "model_id,role,arena_score,rp5h,mapping\n"
        "muse-spark-1.2,candidate,1200,800,\n"
        "claude-opus-5,request,1687.61,,muse-spark-1.2\n"
        "gpt-5.5,request,1500,,\n"
        "gemini-3.7-flash,request,1500,,\n",
        encoding="utf-8",
    )
    warnings: list[dict] = []

    requests = load_csv_requests(path, warnings)

    assert requests == [{"model_id": "claude-opus-5"}]
    assert {w["model"] for w in warnings} == {"gpt-5.5", "gemini-3.7-flash"}
    assert all(w["type"] == "non_claude_request_ignored" for w in warnings)


def test_load_arena_normalizes_raw_board_names(tmp_path: Path) -> None:
    # The collector stores raw display names; the planner owns normalization
    # (case/spacing, date suffixes, effort extraction). Hand-assigned ids are
    # already normalized and pass through idempotently.
    doc = {
        "schema_version": 1,
        "models": {
            "GPT-5.4 Mini (20250320)": {"arena_score": 1500.0},
            "Claude-Opus-5-max": {"arena_score": 1600.0},
            "ling-3.0-flash": {"arena_score": 1458.0, "manual": True},
        },
    }
    path = tmp_path / "arena.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    lookup = load_arena(path)

    assert set(lookup) == {"gpt-5.4-mini", "claude-opus-5", "ling-3.0-flash"}
    assert lookup["claude-opus-5"]["rating"] == 1600.0
    assert lookup["claude-opus-5"]["effort"] == "max"
    assert lookup["ling-3.0-flash"]["effort"] is None


def test_load_csv_requests_missing_file_is_blocking(tmp_path: Path) -> None:
    with pytest.raises(PlanningError):
        load_csv_requests(tmp_path / "missing.csv", [])


def test_load_csv_requests_duplicate_request_is_blocking(tmp_path: Path) -> None:
    path = tmp_path / "models.csv"
    path.write_text(
        "model_id,role,arena_score,rp5h,mapping\n"
        "claude-opus-5,request,,,\n"
        "claude-opus-5,request,,,\n",
        encoding="utf-8",
    )

    with pytest.raises(PlanningError):
        load_csv_requests(path, [])


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
    assert plan["channels"]["commandcode-goat"]["supportedModels"] == [
        "deepseek-v4-flash",
        "kimi-k2.5",
    ]
    assert "providers" not in plan


def test_free_fill_recomputes_from_owning_channel() -> None:
    # The free model belongs to the goat channel, so its quotas must derive
    # from goat's non-free max (500), not from any other channel's values.
    sections = {
        "commandcode-goat": {"freebie": _rec(rp5h=None, quota=None, free=True), "paid": _rec(rp5h=500)},
    }

    _rows, plan = _plan(sections, arena={"paid": 1500.0})

    models = {m["modelID"]: m for m in plan["models"]}
    remark = json.loads(models["freebie"]["input"]["remark"])
    assert remark["rp5h"] == 500
    assert remark["usage_quota"] == 60
    assert any(w["type"] == "free_default_filled" and w["provider"] == "commandcode-goat" for w in plan["warnings"])


def test_variant_suffix_candidate_inherits_base_score() -> None:
    # -vl on the candidate side reaches the base model's hand-assigned
    # arena record through the chain's variant-suffix layer; same-model
    # inheritance is accepted silently (ADR 0015).
    sections = {"ant": {"ling-3.0-flash-vl": _rec(name="Ling 3.0 Flash VL", rp5h=500)}}
    arena = {"ling-3.0-flash": 1458.0}

    rows, plan = _plan(sections, arena=arena)

    candidates = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    assert candidates["ling-3.0-flash-vl"]["arena_score"] == "1458"
    assert not any(w["type"] == "candidate_arena_fallback" for w in plan["warnings"])


def test_unrecognized_variant_suffix_surfaces_for_triage() -> None:
    # A never-seen alphabetic suffix on an unmatched model is raised for
    # human review instead of silently taking the 1500 default.
    sections = {"ant": {"ling-3.0-flash-vq": _rec(name="Ling 3.0 Flash VQ", rp5h=500)}}
    arena = {"ling-3.0-flash": 1458.0}

    _rows, plan = _plan(sections, arena=arena)

    assert any(
        w["type"] == "unrecognized_variant_suffix"
        and w["model"] == "ling-3.0-flash-vq"
        and w["suffix"] == "vq"
        for w in plan["warnings"]
    )


def test_free_declaration_and_rp5h_fallback() -> None:
    # Freeness comes from the record flag, not the id; a channel with no
    # non-free rp5h basis fills the free pool with the 1000 default.
    sections = {
        "ant": {
            "ling-3.0-flash": _rec(rp5h=None, quota=None, free=True),
            "qwen3.8-flash": _rec(rp5h=None),
        },
    }
    arena = {"ling-3.0-flash": 1458.0}

    rows, plan = _plan(sections, arena=arena)

    models = {m["modelID"]: m for m in plan["models"]}
    remark = json.loads(models["ling-3.0-flash"]["input"]["remark"])
    assert remark["rp5h"] == 1000
    assert remark["usage_quota"] == 60
    candidates = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    assert candidates["ling-3.0-flash"]["arena_score"] == "1458"
    assert any(w["type"] == "free_default_filled" and w["provider"] == "ant" for w in plan["warnings"])


def test_free_record_zeroes_silent_cost_fields() -> None:
    # `free: true` declares every price zero: card list prices may not leak
    # into cost fields the channel leaves silent; a non-free control keeps
    # the card values.
    sections = {
        "ant": {
            "free-a": _rec(rp5h=None, quota=None, free=True, cost={}),
            "paid": _rec(rp5h=500, cost={}),
        },
    }
    card = {"name": "Same Card", "cost": {"input": 2, "output": 8, "cache_read": 0.2, "cache_write": 0.8}}

    _rows, plan = _plan(sections, cards={"free-a": card, "paid": dict(card)}, arena={})

    models = {m["modelID"]: m for m in plan["models"]}
    assert models["free-a"]["input"]["cost"] == {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
    }
    assert models["paid"]["input"]["cost"] == {
        "input": 2,
        "output": 8,
        "cacheRead": 0.2,
        "cacheWrite": 0.8,
    }


def test_claude_mapping_uses_formula_and_baseline_routing() -> None:
    sections = {
        "opencode-go": {
            "muse-spark-1.2": _rec(rp5h=800),      # arena 1200, close to haiku
            "qwen3.8-max": _rec(rp5h=100),          # arena 1600, far above
            "freebie": _rec(rp5h=500, free=True),   # free, arena 1500 default
        },
    }
    requests = [
        {"model_id": "claude-haiku-4-5"},
        {"model_id": "claude-opus-5"},
    ]
    arena = {"muse-spark-1.2": 1200.0, "qwen3.8-max": 1600.0, "claude-haiku-4-5": 1150.0, "claude-opus-5": 1700.0}

    rows, plan = _plan(sections, cards={}, arena=arena, requests=requests)

    request_rows = _request_rows(rows)
    # Free fill: the lowest-scored request takes the lowest-scored free model.
    assert request_rows["claude-haiku-4-5"]["mapping"] == "freebie"
    mapping_by_request = {m["request_model"]: m for m in plan["report"]["mappings"]}
    assert mapping_by_request["claude-haiku-4-5"]["match_confidence"] == "free_fill"
    # Free pool exhausted: the remaining request uses the formula over
    # non-free candidates (closest score wins over the far-above one).
    assert request_rows["claude-opus-5"]["mapping"] == "qwen3.8-max"
    # Candidates are listed for review with their matched arena score.
    candidate_by_id = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    assert candidate_by_id["muse-spark-1.2"]["arena_score"] == "1200"
    # fill_free_records re-derived the free quota from the owning channel max.
    assert candidate_by_id["freebie"]["rp5h"] == "800"


def test_channel_cost_wins_over_card_cost() -> None:
    cards = {"deepseek-v4-flash": {"name": "DeepSeek V4 Flash", "cost": {"input": 0.1, "output": 0.5}, "reasoning": True, "limit": {"context": 128000, "output": 8192}}}
    sections = {"commandcode-goat": {"deepseek-v4-flash": _rec(rp5h=18200, quota=60, cost={"input": 0.22, "output": 0.66})}}

    _rows, plan = _plan(sections, cards=cards)

    entry = plan["models"][0]
    assert entry["input"]["cost"] == {"input": 0.22, "output": 0.66, "cacheRead": 0, "cacheWrite": 0}
    assert entry["cardRef"] == "deepseek-v4-flash"


def test_missing_card_warns_and_falls_back_to_channel_name() -> None:
    sections = {"commandcode-goat": {"goat-only-model": _rec(rp5h=100, name="Goat Only Model")}}

    _rows, plan = _plan(sections)

    assert any(w["type"] == "card_missing" and w["model"] == "goat-only-model" for w in plan["warnings"])
    assert plan["models"][0]["input"]["name"] == "Goat Only Model"


def test_manual_exclude_on_record_skips_model() -> None:
    sections = {
        "commandcode-goat": {
            "minimax-m2.5": _rec(rp5h=None, exclude="Missing mapping-critical RP5H"),
            "kimi-k2.5": _rec(rp5h=900),
        },
    }

    _rows, plan = _plan(sections)

    assert plan["channels"]["commandcode-goat"]["supportedModels"] == ["kimi-k2.5"]
    assert all(m["modelID"] != "minimax-m2.5" for m in plan["models"])
    assert any(
        w["type"] == "manual_excluded" and w["model"] == "minimax-m2.5"
        and w["reason"] == "Missing mapping-critical RP5H"
        for w in plan["warnings"]
    )
    assert "removals" not in plan


def test_speed_variant_ids_are_derived_as_excluded() -> None:
    sections = {
        "commandcode-goat": {
            "glm-5.2-fast": _rec(rp5h=138),
            "kimi-k2.5": _rec(rp5h=900),
        },
    }

    _rows, plan = _plan(sections)

    assert all(m["modelID"] != "glm-5.2-fast" for m in plan["models"])
    assert plan["channels"]["commandcode-goat"]["supportedModels"] == ["kimi-k2.5"]
    assert any(
        w["type"] == "speed_variant_excluded" and w["model"] == "glm-5.2-fast"
        for w in plan["warnings"]
    )


def test_request_without_arena_score_is_blocking() -> None:
    sections = {"opencode-go": {"muse-spark-1.2": _rec(rp5h=800)}}

    _rows, plan = _plan(sections, requests=[{"model_id": "claude-opus-5"}])

    assert any(e["code"] == "request_arena_missing" for e in plan["report"]["errors"])
    assert plan["report"]["counts"]["errors"] >= 1


def test_alias_merges_cross_channel_naming() -> None:
    aliases = {"tencent-hy3": "hy3"}
    sections = {
        "opencode-go": {"hy3": _rec(rp5h=4300)},
        "commandcode-goat": {"tencent-hy3": _rec(rp5h=7080)},
    }

    _rows, channel_plan, model_plan, _report = plan_from(
        sections,
        aliases=aliases,
        cards={},
        arena_models={},
        requests=[],
    )

    # One canonical model, owned by the goat channel (7080 > 4300).
    models = {m["modelID"]: m for m in model_plan["models"]}
    assert set(models) == {"hy3"}
    assert models["hy3"]["channel"] == "commandcode-goat"
    assert models["hy3"]["channelAliases"] == {"commandcode-goat": "tencent-hy3"}
    # Channel lists keep the native id each channel actually exposes.
    assert channel_plan["channels"]["commandcode-goat"]["supportedModels"] == ["tencent-hy3"]
    assert channel_plan["channels"]["opencode-go"]["supportedModels"] == []


def test_free_variant_supersedes_plain_original() -> None:
    sections = {
        "opencode-go": {"longcat-2.0": _rec(rp5h=11400)},
        "commandcode-goat": {"longcat-2.0-free": _rec(rp5h=None, free=True)},
    }

    _rows, plan = _plan(sections)

    models = {m["modelID"]: m for m in plan["models"]}
    assert set(models) == {"longcat-2.0-free"}
    assert any(
        w["type"] == "variant_superseded" and w["model"] == "longcat-2.0" and w["replaced_by"] == "longcat-2.0-free"
        for w in plan["warnings"]
    )
    assert plan["channels"]["opencode-go"]["supportedModels"] == []
    assert plan["channels"]["commandcode-goat"]["supportedModels"] == ["longcat-2.0-free"]


def test_contributor_variant_supersedes_plain_original() -> None:
    sections = {
        "commandcode-goat": {
            "muse-spark-1.2": _rec(rp5h=428),
            "muse-spark-1.2-contributor": _rec(rp5h=18200),
        },
    }

    _rows, plan = _plan(sections)

    models = {m["modelID"] for m in plan["models"]}
    assert models == {"muse-spark-1.2-contributor"}
    assert any(w["type"] == "variant_superseded" and w["model"] == "muse-spark-1.2" for w in plan["warnings"])


def test_rp5h_missing_low_arena_excluded_high_arena_review() -> None:
    sections = {
        "commandcode-goat": {
            "glm-5": _rec(rp5h=None),          # arena 1435 < 1500 -> excluded
            "kimi-k2.5": _rec(rp5h=None),      # no arena standing -> excluded too
        },
    }
    arena = {"glm-5": 1435.72}

    _rows, _channel_plan, model_plan, _report = plan_from(sections, cards={}, arena_models=_arena_models(arena), requests=[])

    models = {m["modelID"] for m in model_plan["models"]}
    assert models == set()
    assert any(w["type"] == "rp5h_missing_excluded" and w["model"] == "glm-5" for w in _report["warnings"])
    # No invented 1500: an unlisted model has no standing, so the missing-rp5h
    # triage excludes it instead of reviewing it (ADR 0015).
    assert any(w["type"] == "arena_missing" and w["model"] == "kimi-k2.5" for w in _report["warnings"])
    assert any(w["type"] == "rp5h_missing_excluded" and w["model"] == "kimi-k2.5" for w in _report["warnings"])


def test_missing_arena_leaves_candidate_unscored_out_of_mapping() -> None:
    sections = {"opencode-go": {"omen-alpha": _rec(rp5h=11600)}}
    requests = [{"model_id": "claude-sonnet-5"}]
    arena = {"claude-sonnet-5": 1536.91, "muse-spark-1.3-contributor": 1622.48}
    sections["opencode-go"]["muse-spark-1.3-contributor"] = _rec(rp5h=45300)

    rows, _channel_plan, _model_plan, report = plan_from(
        sections,
        cards={},
        arena_models=_arena_models(arena),
        requests=requests,
    )

    assert any(w["type"] == "arena_missing" and w["model"] == "omen-alpha" for w in report["warnings"])
    candidate_by_id = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    # Listed for review, but with no invented score.
    assert candidate_by_id["omen-alpha"]["arena_score"] == ""
    # The unscored candidate cannot win the formula: the only scored
    # non-free candidate takes the request.
    assert _request_rows(rows)["claude-sonnet-5"]["mapping"] == "muse-spark-1.3-contributor"


def test_borrowed_arena_scores_are_rejected() -> None:
    # version_downgrade (qwen3.7-plus -> qwen3.6-plus) and prefix_match
    # (qwen3.8-flash -> the qwen3.8-* family) borrow another model's
    # standing; both are rejected with an empty score (ADR 0015).
    sections = {
        "commandcode-goat": {
            "qwen3.7-plus": _rec(rp5h=4300),
            "qwen3.8-flash": _rec(rp5h=5400),
        },
    }
    arena = {"qwen3.6-plus": 1460.01, "qwen3.8-flash-27b": 1600.0}

    rows, plan = _plan(sections, arena=arena)

    rejected = {w["model"]: w["match_type"] for w in plan["warnings"] if w["type"] == "arena_borrowed_rejected"}
    assert rejected == {"qwen3.7-plus": "version_downgrade", "qwen3.8-flash": "prefix_match"}
    candidates = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    assert candidates["qwen3.7-plus"]["arena_score"] == ""
    assert candidates["qwen3.8-flash"]["arena_score"] == ""


def test_channel_priority_orders_by_rp5h_and_counts_intersection() -> None:
    # Two collected channels declare the same canonical (via the alias map):
    # the higher rp5h is p0, the null-rp5h static section trails last.
    sections = {
        "commandcode-goat": {"tencent-hy3": _rec(rp5h=7080)},
        "opencode-go": {"hy3": _rec(rp5h=4300)},
        "ant": {"hy3": _rec(rp5h=None)},
        "sensenova": {"solo": _rec(rp5h=100)},
    }

    _rows, _channel_plan, model_plan, report = plan_from(
        sections,
        aliases={"tencent-hy3": "hy3"},
        cards={},
        arena_models=_arena_models({}),
        requests=[],
    )

    models = {m["modelID"]: m for m in model_plan["models"]}
    assert [step["channel"] for step in models["hy3"]["channelPriority"]] == [
        "commandcode-goat", "opencode-go", "ant",
    ]
    assert [step["priority"] for step in models["hy3"]["channelPriority"]] == [0, 1, 2]
    assert models["hy3"]["channelPriority"][0]["rp5h"] == 7080.0
    assert models["hy3"]["channelPriority"][2]["rp5h"] is None
    # Single-channel models degrade to a p0 pin.
    assert [step["channel"] for step in models["solo"]["channelPriority"]] == ["sensenova"]
    assert model_plan["report"]["counts"]["intersection"] == 1


def test_plans_split_warnings_by_artifact() -> None:
    # ADR 0017: channel-plan carries the allowlist warnings, model-plan the
    # card/remark ones, and arena/mapping warnings stay out of both.
    sections = {
        "opencode-go": {"omen-alpha": _rec(rp5h=11600)},
        "commandcode-goat": {"omen-alpha": _rec(rp5h=900), "free-a": _rec(rp5h=None, free=True)},
    }
    arena = {"omen-alpha": 1460.01}

    _rows, channel_plan, model_plan, report = plan_from(
        sections,
        cards={},
        arena_models=_arena_models(arena),
        requests=[],
    )

    assert channel_plan["schema_version"] == 1
    channel_types = {w["type"] for w in channel_plan["warnings"]}
    assert "duplicate_model_across_sources" in channel_types
    assert "free_default_filled" in channel_types
    assert model_plan["schema_version"] == 1
    model_types = {w["type"] for w in model_plan["warnings"]}
    assert "card_missing" in model_types
    assert not (channel_types & model_types)
    # Arena and mapping workflow warnings never enter an artifact.
    assert "arena_defaulted" in {w["type"] for w in report["warnings"]}
    assert all("arena" not in t for t in channel_types | model_types)


def test_free_fill_pairs_lowest_request_with_lowest_free_model() -> None:
    # User example: haiku (lowest request) gets laguna (lowest free), then
    # sonnet-4-6 gets longcat; the rest fall through to the formula.
    sections = {
        "commandcode-goat": {
            "laguna-s-2.1-free": _rec(rp5h=None, free=True),
            "longcat-2.0-free": _rec(rp5h=None, free=True),
            "muse-spark-1.3-contributor": _rec(rp5h=45300),
            "paid-filler": _rec(rp5h=900),
        },
        "opencode-go": {"longcat-2.0": _rec(rp5h=1540)},
    }
    requests = [
        {"model_id": "claude-haiku-4-5"},
        {"model_id": "claude-sonnet-4-6"},
        {"model_id": "claude-opus-5"},
    ]
    arena = {
        "claude-haiku-4-5": 1328.9,
        "claude-sonnet-4-6": 1521.49,
        "claude-opus-5": 1687.61,
        "muse-spark-1.3-contributor": 1622.48,
        "longcat-2.0": 1540,
        "paid-filler": 1200.0,
    }

    rows, _channel_plan, _model_plan, report = plan_from(
        sections,
        cards={},
        arena_models=_arena_models(arena),
        requests=requests,
    )

    request_rows = _request_rows(rows)
    assert request_rows["claude-haiku-4-5"]["mapping"] == "laguna-s-2.1-free"
    assert request_rows["claude-sonnet-4-6"]["mapping"] == "longcat-2.0-free"
    # Free pool exhausted -> formula over non-free candidates.
    assert request_rows["claude-opus-5"]["mapping"] == "muse-spark-1.3-contributor"
    mapping_by_request = {m["request_model"]: m for m in report["mappings"]}
    assert mapping_by_request["claude-haiku-4-5"]["match_confidence"] == "free_fill"
    assert mapping_by_request["claude-opus-5"]["match_confidence"] in ("high", "medium", "none")


def test_score_formula_ignores_price_and_quota() -> None:
    candidate = {"model_id": "a", "arena_score": 1200.0, "rp5h": 800.0}
    priced = dict(candidate, cost={"input": 99.0}, usage_quota=1)

    assert score_match(1150.0, candidate, {"max_score": 1600.0, "max_rp5h": 1000.0, "max_score_diff": 500.0}) == (
        score_match(1150.0, priced, {"max_score": 1600.0, "max_rp5h": 1000.0, "max_score_diff": 500.0})
    )


def test_main_round_trips_csv_request_rows_and_writes_plan(tmp_path: Path) -> None:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "channels": {"opencode-go": {"muse-spark-1.2": _rec(rp5h=800)}},
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps(_arena_doc({"muse-spark-1.2": 1200.0, "claude-opus-5": 1700.0})), encoding="utf-8")
    cards = tmp_path / "all_models.json"
    cards.write_text(json.dumps({}), encoding="utf-8")
    csv_path = tmp_path / "models.csv"
    csv_path.write_text(
        "model_id,role,arena_score,rp5h,mapping\n"
        "claude-opus-5,request,,,\n",
        encoding="utf-8",
    )
    plan_out = tmp_path / "plan.json"

    rc = main([
        "--extra", str(extra), "--cards", str(cards), "--arena", str(arena),
        "--csv", str(csv_path),
        "--channel-plan", str(tmp_path / "channel-plan.json"),
        "--model-plan", str(plan_out),
    ])

    assert rc == 0
    text = csv_path.read_text(encoding="utf-8")
    # The request row keeps its id; arena score and mapping are recomputed.
    assert "claude-opus-5,request,1700,," in text
    assert "muse-spark-1.2,candidate,1200,800," in text
    channel_plan = json.loads((tmp_path / "channel-plan.json").read_text(encoding="utf-8"))
    assert channel_plan["schema_version"] == 1
    assert channel_plan["channels"]["opencode-go"]["supportedModels"] == ["muse-spark-1.2"]
    model_plan = json.loads(plan_out.read_text(encoding="utf-8"))
    assert model_plan["schema_version"] == 1
    entry = model_plan["models"][0]
    assert entry["modelID"] == "muse-spark-1.2"
    assert "modelCard" not in entry["input"]
    assert entry["input"]["cost"] == {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    assert "removals" not in model_plan and "providers" not in model_plan


def test_main_fail_on_errors_exits_nonzero(tmp_path: Path) -> None:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "channels": {"opencode-go": {"muse-spark-1.2": _rec(rp5h=800)}},
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps(_arena_doc({"muse-spark-1.2": 1200.0})), encoding="utf-8")
    cards = tmp_path / "cards.json"
    cards.write_text("{}", encoding="utf-8")
    csv_path = tmp_path / "models.csv"
    csv_path.write_text(
        "model_id,role,arena_score,rp5h,mapping\n"
        "claude-opus-5,request,,,\n",
        encoding="utf-8",
    )

    rc = main([
        "--extra", str(extra), "--cards", str(cards), "--arena", str(arena),
        "--csv", str(csv_path),
        "--channel-plan", str(tmp_path / "channel-plan.json"),
        "--model-plan", str(tmp_path / "model-plan.json"),
        "--fail-on-errors",
    ])

    assert rc == 1
