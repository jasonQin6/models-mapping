#!/usr/bin/env python3
"""Regression tests for the deterministic three-source mapping builder."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_mapping import build_mapping_data, score_match


def envelope(models: dict) -> dict:
    return {"schema_version": 1, "models": models}


def models_snapshot(*model_ids: str) -> dict:
    return {
        "schema_version": 1,
        "providers": {
            "opencode-go": {
                "models": {model_id: {"id": model_id} for model_id in model_ids}
            }
        },
    }


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


def test_mapping_uses_only_arena_and_rp5h_for_candidate_eligibility() -> None:
    rows, report = build_mapping_data(
        models_snapshot("candidate-a"),
        envelope({"candidate-a": {"rp5h": 100}}),
        arena_snapshot({"candidate-a": 1500, "gpt-5.5": 1500}),
        request_config("gpt-5.5"),
    )

    request = next(row for row in rows if row["role"] == "request")
    assert request["mapping"] == "candidate-a"
    assert report["errors"] == []


def test_catalog_uses_cache_go_intersection() -> None:
    rows, report = build_mapping_data(
        models_snapshot("cache-only"),
        envelope({"go-only": {"rp5h": 50}}),
        arena_snapshot({"go-only": 1400, "gpt-5.5": 1500}),
        request_config("gpt-5.5"),
    )

    assert not [row for row in rows if row["role"] == "candidate"]
    assert {item["reason"] for item in report["excluded"]} == {
        "absent_in_go",
        "absent_in_models",
    }


def test_baseline_prefers_highest_rp5h_free_candidate() -> None:
    rows, report = build_mapping_data(
        models_snapshot("free-low", "free-high", "paid"),
        envelope(
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


def test_cache_only_free_model_is_excluded() -> None:
    rows, report = build_mapping_data(
        models_snapshot("ox-alpha-free", "paid"),
        envelope({"paid": {"rp5h": 900}}),
        arena_snapshot({"paid": 1500, "gpt-5.4": 1400}),
        request_config("gpt-5.4"),
    )

    candidates = {row["model_id"]: row for row in rows if row["role"] == "candidate"}
    request = next(row for row in rows if row["role"] == "request")
    assert "ox-alpha-free" not in candidates
    assert request["mapping"] == "paid"
    assert any(
        item["model_id"] == "ox-alpha-free"
        and item["reason"] == "absent_in_go"
        for item in report["excluded"]
    )
    assert report["errors"] == []


def test_missing_request_arena_is_blocking() -> None:
    rows, report = build_mapping_data(
        models_snapshot("candidate-a"),
        envelope({"candidate-a": {"rp5h": 100}}),
        arena_snapshot({"candidate-a": 1500}),
        request_config("gpt-unknown"),
    )

    request = next(row for row in rows if row["role"] == "request")
    assert request["mapping"] == ""
    assert any(error["code"] == "request_arena_missing" for error in report["errors"])


def test_invalid_schema_and_arena_score_are_blocking() -> None:
    _, report = build_mapping_data(
        {"providers": {}},
        envelope({"candidate-a": {"rp5h": 100}}),
        envelope({"candidate-a": {"arena_score": "bad"}}),
        request_config("gpt-5.5"),
    )

    codes = {error["code"] for error in report["errors"]}
    assert "unsupported_schema_version" in codes
    assert "invalid_arena_score" in codes


def test_score_formula_uses_log_rp5h_and_no_price_or_quota() -> None:
    max_values = {"max_score": 1500, "max_rp5h": 1000, "max_score_diff": 500}
    base = {"arena_score": 1500, "rp5h": 100, "usage_quota": 1, "price_output": 100}
    alternate = {**base, "usage_quota": 60, "price_output": 0}

    assert score_match(1500, base, max_values) == score_match(
        1500, alternate, max_values
    )
