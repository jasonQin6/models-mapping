#!/usr/bin/env python3
"""Regression tests for the deterministic mapping builder (union candidate universe)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_mapping import build_mapping_data, score_match


def all_models_snapshot(*model_ids: str) -> dict:
    return {f"vendor/{mid}": {"id": f"vendor/{mid}", "name": mid} for mid in model_ids}


def opencode_go_snapshot(models: dict) -> dict:
    return {"opencode-go": {"id": "opencode-go", "models": {k: {"id": k, **v} for k, v in models.items()}}}


def goat_snapshot(models: dict) -> dict:
    return {"commandcode-goat": {"id": "commandcode-goat", "models": {k: {"id": k, **v} for k, v in models.items()}}}


def envelope(models: dict) -> dict:
    return {"schema_version": 1, "models": models}


def arena_snapshot(scores: dict[str, float]) -> dict:
    return envelope(
        {
            model_id: {"arena_score": score, "arena_rank": index + 1}
            for index, (model_id, score) in enumerate(scores.items())
        }
    )


def request_config(*model_ids: str) -> dict:
    return {
        "schema_version": 1,
        "models": [
            {"model_id": model_id, "enabled": True} for model_id in model_ids
        ],
    }


def run(models, go, arena, requests, goat=None):
    rows, report, enrichment = build_mapping_data(
        models, go, arena, requests, None, goat
    )
    return rows, report, enrichment


def test_mapping_uses_only_arena_and_rp5h_for_candidate_eligibility() -> None:
    rows, report, _ = run(
        all_models_snapshot("candidate-a"),
        opencode_go_snapshot({"candidate-a": {"rp5h": 100}}),
        arena_snapshot({"candidate-a": 1500, "gpt-5.5": 1500}),
        request_config("gpt-5.5"),
    )

    request = next(row for row in rows if row["role"] == "request")
    assert request["mapping"] == "candidate-a"
    assert report["errors"] == []


def test_union_candidate_universe_includes_goat_models() -> None:
    rows, report, _ = run(
        all_models_snapshot("candidate-a"),
        opencode_go_snapshot({"candidate-a": {"rp5h": 100}}),
        arena_snapshot({"candidate-a": 1500, "goat-only": 1400, "gpt-5.5": 1500}),
        request_config("gpt-5.5"),
        goat_snapshot({"goat-only": {"rp5h": 800}}),
    )

    candidates = {row["model_id"] for row in rows if row["role"] == "candidate"}
    assert candidates == {"candidate-a", "goat-only"}
    goat_meta = next(
        row for row in rows if row["role"] == "candidate" and row["model_id"] == "goat-only"
    )
    assert goat_meta["rp5h"] == "800"  # CSV values are strings
    assert report["errors"] == []


def test_candidate_without_arena_is_ineligible() -> None:
    rows, report, _ = run(
        all_models_snapshot("candidate-a", "no-arena"),
        opencode_go_snapshot(
            {"candidate-a": {"rp5h": 100}, "no-arena": {"rp5h": 200}}
        ),
        arena_snapshot({"candidate-a": 1500, "gpt-5.5": 1500}),
        request_config("gpt-5.5"),
    )

    candidates = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    assert "no-arena" in candidates
    assert any(
        item["model_id"] == "no-arena" and item["reason"] == "missing_arena"
        for item in report["ineligible"]
    )
    request = next(row for row in rows if row["role"] == "request")
    assert request["mapping"] == "candidate-a"


def test_candidate_without_rp5h_is_ineligible() -> None:
    rows, report, _ = run(
        all_models_snapshot("candidate-a", "no-quota"),
        opencode_go_snapshot(
            {"candidate-a": {"rp5h": 100}, "no-quota": {}}
        ),
        arena_snapshot({"candidate-a": 1500, "no-quota": 1450, "gpt-5.5": 1500}),
        request_config("gpt-5.5"),
    )

    assert any(
        item["model_id"] == "no-quota" and item["reason"] == "missing_rp5h"
        for item in report["ineligible"]
    )
    request = next(row for row in rows if row["role"] == "request")
    assert request["mapping"] == "candidate-a"


def test_baseline_prefers_highest_rp5h_free_candidate() -> None:
    rows, report, _ = run(
        all_models_snapshot("free-low", "free-high", "paid"),
        opencode_go_snapshot(
            {
                "free-low": {"rp5h": 100},
                "free-high": {"rp5h": 500},
                "paid": {"rp5h": 1000},
            }
        ),
        arena_snapshot(
            {
                "free-low": 0,
                "free-high": 0,
                "paid": 1500,
                "gpt-5.4": 1400,
                "gpt-5.5": 1600,
            }
        ),
        request_config("gpt-5.4", "gpt-5.5"),
    )

    requests = {row["model_id"]: row for row in rows if row["role"] == "request"}
    assert requests["gpt-5.4"]["mapping"] == "free-high"
    assert report["errors"] == []


def test_free_candidate_without_rp5h_gets_channel_maximum() -> None:
    rows, report, enrichment = run(
        all_models_snapshot("free-x", "paid"),
        opencode_go_snapshot({"free-x": {}, "paid": {"rp5h": 900, "usage_quota": 60}}),
        arena_snapshot({"free-x": 1400, "paid": 1500, "gpt-5.4": 1400}),
        request_config("gpt-5.4"),
    )

    assert enrichment["free-x"]["rp5h"] == 900
    assert enrichment["free-x"]["source"] == "opencode-go"
    assert "rp5h" in enrichment["free-x"]["derived"]
    requests = {row["model_id"]: row for row in rows if row["role"] == "request"}
    assert requests["gpt-5.4"]["mapping"] == "free-x" or requests["gpt-5.4"]["mapping"] == "paid"


def test_goat_free_model_fill_recorded_in_enrichment() -> None:
    rows, report, enrichment = run(
        all_models_snapshot("paid"),
        opencode_go_snapshot({"paid": {"rp5h": 900, "usage_quota": 60}}),
        arena_snapshot({"paid": 1500, "goat-free": 1400, "gpt-5.4": 1400}),
        request_config("gpt-5.4"),
        goat_snapshot({"goat-free": {}, "goat-paid": {"rp5h": 700}}),
    )

    assert enrichment["goat-free"]["source"] == "commandcode-goat"
    assert enrichment["goat-free"]["rp5h"] == 700  # goat channel's own maximum
    assert enrichment["goat-free"]["usage_quota"] == 60  # fixed default


def test_missing_request_arena_is_blocking() -> None:
    rows, report, _ = run(
        all_models_snapshot("candidate-a"),
        opencode_go_snapshot({"candidate-a": {"rp5h": 100}}),
        arena_snapshot({"candidate-a": 1500}),
        request_config("gpt-unknown"),
    )

    request = next(row for row in rows if row["role"] == "request")
    assert request["mapping"] == ""
    assert any(error["code"] == "request_arena_missing" for error in report["errors"])


def test_invalid_schema_and_arena_score_are_blocking() -> None:
    _, report, _ = run(
        {},
        opencode_go_snapshot({"candidate-a": {"rp5h": 100}}),
        envelope({"candidate-a": {"arena_score": "bad"}}),
        request_config("gpt-5.5"),
    )

    codes = {error["code"] for error in report["errors"]}
    assert "invalid_models_source" in codes
    assert "invalid_arena_score" in codes


def test_score_formula_uses_log_rp5h_and_no_price_or_quota() -> None:
    max_values = {"max_score": 1500, "max_rp5h": 1000, "max_score_diff": 500}
    base = {"arena_score": 1500, "rp5h": 100, "usage_quota": 1, "price_output": 100}
    alternate = {**base, "usage_quota": 60, "price_output": 0}

    assert score_match(1500, base, max_values) == score_match(
        1500, alternate, max_values
    )
