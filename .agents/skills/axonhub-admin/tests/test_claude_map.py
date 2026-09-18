#!/usr/bin/env python3
"""Tests for the Claude mapping step: formula, free fill, request gating."""

import json
from pathlib import Path

import claude_map  # noqa: E402
from claude_map import build_mappings, compute_mapping_for_request_model, main, score_match  # noqa: E402
from testutil import arena_models, build, rec  # noqa: E402


def _map(sections, requests, arena=None):
    result = build(sections, arena=arena)
    return build_mappings(result, requests, arena_models(arena or {}))


def test_claude_mapping_uses_formula_and_free_fill() -> None:
    sections = {
        "opencode-go": {
            "muse-spark-1.2": rec(rp5h=800),      # arena 1650, closest to opus
            "qwen3.8-max": rec(rp5h=100),          # arena 1600, farther below
            "freebie": rec(rp5h=500, cost={"input": 0, "output": 0}),   # free, arena 1500 default
        },
    }
    requests = [
        {"model_id": "claude-haiku-4-5"},
        {"model_id": "claude-opus-5"},
    ]
    arena = {"muse-spark-1.2": 1650.0, "qwen3.8-max": 1600.0, "claude-haiku-4-5": 1150.0, "claude-opus-5": 1700.0}

    report = _map(sections, requests, arena=arena)

    mapping_by_request = {m["request_model"]: m for m in report["mappings"]}
    # Free fill: the lowest-scored request takes the lowest-scored free model.
    assert mapping_by_request["claude-haiku-4-5"]["suggested_target"] == "freebie"
    assert mapping_by_request["claude-haiku-4-5"]["match_confidence"] == "free_fill"
    # Free pool exhausted: the remaining request uses the formula over
    # non-free candidates (closest score wins over the farther one).
    assert mapping_by_request["claude-opus-5"]["suggested_target"] == "muse-spark-1.2"


def test_free_fill_pairs_lowest_request_with_lowest_free_model() -> None:
    # User example: haiku (lowest request) gets laguna (lowest free), then
    # sonnet-4-6 gets longcat; the rest fall through to the formula.
    sections = {
        "commandcode-goat": {
            "laguna-s-2.1-free": rec(rp5h=None, cost={"input": 0, "output": 0}),
            "longcat-2.0-free": rec(rp5h=None, cost={"input": 0, "output": 0}),
            "muse-spark-1.3-contributor": rec(rp5h=45300),
            "paid-filler": rec(rp5h=900),
        },
        "opencode-go": {"longcat-2.0": rec(rp5h=1540)},
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

    report = _map(sections, requests, arena=arena)

    mapping_by_request = {m["request_model"]: m for m in report["mappings"]}
    assert mapping_by_request["claude-haiku-4-5"]["suggested_target"] == "laguna-s-2.1-free"
    assert mapping_by_request["claude-sonnet-4-6"]["suggested_target"] == "longcat-2.0-free"
    # Free pool exhausted -> formula over non-free candidates.
    assert mapping_by_request["claude-opus-5"]["suggested_target"] == "muse-spark-1.3-contributor"
    assert mapping_by_request["claude-haiku-4-5"]["match_confidence"] == "free_fill"
    assert mapping_by_request["claude-opus-5"]["match_confidence"] in ("high", "medium", "none")


def test_request_without_arena_score_is_blocking() -> None:
    sections = {"opencode-go": {"muse-spark-1.2": rec(rp5h=800)}}

    report = _map(sections, [{"model_id": "claude-opus-5"}])

    assert any(e["code"] == "request_arena_missing" for e in report["errors"])
    assert report["counts"]["errors"] >= 1


def test_unscored_candidate_cannot_win_the_formula() -> None:
    sections = {"opencode-go": {"omen-alpha": rec(rp5h=11600), "muse-spark-1.3-contributor": rec(rp5h=45300)}}
    requests = [{"model_id": "claude-sonnet-5"}]
    arena = {"claude-sonnet-5": 1536.91, "muse-spark-1.3-contributor": 1622.48}

    report = _map(sections, requests, arena=arena)

    # The unscored candidate cannot win the formula: the only scored
    # non-free candidate takes the request.
    mapping_by_request = {m["request_model"]: m for m in report["mappings"]}
    assert mapping_by_request["claude-sonnet-5"]["suggested_target"] == "muse-spark-1.3-contributor"
    assert any(w["type"] == "arena_missing" and w["model"] == "omen-alpha" for w in report["warnings"])


def test_score_formula_ignores_price_and_quota() -> None:
    candidate = {"model_id": "a", "arena_score": 1200.0, "rp5h": 800.0}
    priced = dict(candidate, cost={"input": 99.0}, usage_quota=1)

    assert score_match(1150.0, candidate, {"max_score": 1600.0, "max_rp5h": 1000.0, "max_score_diff": 500.0}) == (
        score_match(1150.0, priced, {"max_score": 1600.0, "max_rp5h": 1000.0, "max_score_diff": 500.0})
    )


def test_compute_mapping_returns_none_without_eligible_candidates() -> None:
    target, details = compute_mapping_for_request_model(1500.0, [{"model_id": "x", "arena_score": None, "rp5h": 5}])

    assert target is None
    assert details is None


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "channels": {"opencode-go": {"muse-spark-1.2": rec(rp5h=800)}},
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps({
        "schema_version": 1,
        "models": {"muse-spark-1.2": {"arena_score": 1520.0}, "claude-opus-5": {"arena_score": 1700.0}},
    }), encoding="utf-8")
    return extra, arena


def test_main_prints_mappings_and_exits_zero(tmp_path: Path, capsys, monkeypatch) -> None:
    extra, arena = _write_inputs(tmp_path)
    monkeypatch.setattr(claude_map, "REQUESTS", {"claude-opus-5": ""})

    rc = main(["--extra", str(extra), "--arena", str(arena)])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mappings"] == [
        {
            "request_model": "claude-opus-5",
            "suggested_target": "muse-spark-1.2",
            "arena_score": 1700.0,
            "match_confidence": "high",
        }
    ]


def test_main_blocking_request_exits_nonzero(tmp_path: Path, capsys, monkeypatch) -> None:
    extra, arena = _write_inputs(tmp_path)
    monkeypatch.setattr(claude_map, "REQUESTS", {"claude-fable-5": ""})  # not on the board

    rc = main(["--extra", str(extra), "--arena", str(arena)])

    assert rc == 1
    captured = capsys.readouterr()
    assert "request_arena_missing" in captured.err
    # Mappings still print for review.
    assert json.loads(captured.out)["counts"]["errors"] == 1
