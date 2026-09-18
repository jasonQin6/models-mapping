#!/usr/bin/env python3
"""Tests for the shared snapshot loaders (axonhub-admin)."""

import json
from pathlib import Path

from snapshot import index_blocklist, load_arena, load_blocklist  # noqa: E402


def test_load_arena_normalizes_raw_board_names(tmp_path: Path) -> None:
    # The collector stores raw display names; the loader owns normalization
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


def test_blocklist_loader_returns_raw_shape(tmp_path: Path) -> None:
    doc = {
        "schema_version": 1,
        "blocklist": {"opencode-go": [{"id": "google/gemini-3.5-flash-lite", "reason": "tier"}]},
    }
    path = tmp_path / "blocklist.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    rules = load_blocklist(path)

    assert rules["opencode-go"] == [{"id": "google/gemini-3.5-flash-lite", "reason": "tier"}]
    # The lookup index spells both the full and the bare (vendor-prefix-stripped)
    # form so collected bare lowercase ids match.
    indexed = index_blocklist(rules)["opencode-go"]
    assert indexed["google/gemini-3.5-flash-lite"] == "tier"
    assert indexed["gemini-3.5-flash-lite"] == "tier"
