#!/usr/bin/env python3
"""Tests for the OpenCode Go enricher (merges go.mdx into opencode-go-models.json)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from watch_go import (
    fix_free_models,
    merge_into_opencode_go,
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


def test_merge_into_opencode_go_injects_and_clears_fields(tmp_path: Path) -> None:
    path = tmp_path / "opencode-go-models.json"
    path.write_text(
        json.dumps(
            {
                "opencode-go": {
                    "id": "opencode-go",
                    "models": {
                        "keep": {"id": "keep", "cost": {"input": 1}, "rp5h": 999, "price_input": 2},
                        "enrich": {"id": "enrich", "cost": {"input": 1}},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    payload = merge_into_opencode_go(
        path,
        {
            "enrich": {"rp5h": 100, "price_input": 0.95, "price_cached_write": 0.375, "protocol": "completions"},
        },
    )

    enriched = payload["opencode-go"]["models"]["enrich"]
    assert enriched["rp5h"] == 100
    assert enriched["price_input"] == 0.95
    assert enriched["price_cached_write"] == 0.375
    # Fields without a downstream consumer are not stored even when parsed.
    assert "protocol" not in enriched
    assert "retention" not in enriched
    assert "rp5h" not in payload["opencode-go"]["models"]["keep"]
    assert "price_input" not in payload["opencode-go"]["models"]["keep"]


def test_write_json_atomic_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    payload = {"a": 1}
    write_json_atomic(path, payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
