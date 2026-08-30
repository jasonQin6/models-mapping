#!/usr/bin/env python3
"""Tests for the OpenCode Go JSON watcher."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from watch_go import (
    build_go_snapshot,
    fix_free_models,
    preserve_timestamp_when_unchanged,
    write_json_atomic,
)


def test_fix_free_uses_largest_quota_and_zero_prices() -> None:
    models = {
        "paid": {"rp5h": 900, "usage_quota": 60, "price_output": 2},
        "ox-alpha-free": {"rp5h": None, "usage_quota": None},
    }

    fixed = fix_free_models(models)

    assert fixed["ox-alpha-free"]["rp5h"] == 900
    assert fixed["ox-alpha-free"]["usage_quota"] == 60
    assert fixed["ox-alpha-free"]["price_output"] == 0


def test_unchanged_snapshot_preserves_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "go.json"
    old = build_go_snapshot(
        {"model": {"rp5h": 100}},
        source_commit="abc",
        fetched_at="2026-01-01T00:00:00+00:00",
    )
    write_json_atomic(path, old)
    new = build_go_snapshot(
        {"model": {"rp5h": 100}},
        source_commit="abc",
        fetched_at="2026-01-02T00:00:00+00:00",
    )

    stable = preserve_timestamp_when_unchanged(path, new)

    assert stable["source"]["fetched_at"] == "2026-01-01T00:00:00+00:00"
