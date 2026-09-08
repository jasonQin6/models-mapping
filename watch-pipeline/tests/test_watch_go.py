#!/usr/bin/env python3
"""Tests for the OpenCode Go watcher (builds opencode-go-models.json from go.mdx)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from watch_go import (
    build_opencode_go_snapshot,
    fix_free_models,
    load_models_dev_provider,
    parse_go_mdx,
    write_json_atomic,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_fix_free_uses_largest_quota_and_zero_prices() -> None:
    models = {
        "paid": {"rp5h": 900, "usage_quota": 60, "price_output": 2},
        "ox-alpha-free": {"rp5h": None, "usage_quota": None},
    }

    fixed = fix_free_models(models)

    assert fixed["ox-alpha-free"]["rp5h"] == 900
    assert fixed["ox-alpha-free"]["usage_quota"] == 60
    assert fixed["ox-alpha-free"]["price_output"] == 0


def test_parse_go_mdx_fixture_yields_model_ids_and_quotas() -> None:
    models = parse_go_mdx((FIXTURES / "go-sample.mdx").read_text(encoding="utf-8"))

    assert set(models) == {"grok-4.6", "sample-bot", "freebie"}
    assert models["grok-4.6"]["rp5h"] == 169
    assert models["sample-bot"]["usage_quota"] == 60
    assert models["sample-bot"]["price_cached_write"] == 0.375
    # Free model: quotas filled from the largest non-free value, prices zero.
    assert models["freebie"]["rp5h"] == 1000
    assert models["freebie"]["usage_quota"] == 60
    assert models["freebie"]["price_input"] == 0


def test_build_snapshot_go_mdx_owns_the_model_list() -> None:
    go_models = {
        "grok-4.6": {"model_id": "grok-4.6", "name": "Grok 4.6", "rp5h": 169, "usage_quota": 20},
        "sample-bot": {"model_id": "sample-bot", "name": "Sample Bot", "rp5h": 1000, "usage_quota": 60},
    }
    models_dev = {
        "grok-4.6": {
            "id": "grok-4.6",
            "name": "Grok 4.6 (latest)",
            "description": "upstream card",
            "cost": {"input": 0.2},
            "limit": {"context": 128000},
        },
        "modelsdev-only": {"id": "modelsdev-only", "name": "Undocumented"},
    }
    previous = {"opencode-go": {"id": "opencode-go", "api": "https://example.test", "models": {}}}

    payload, enriched = build_opencode_go_snapshot(go_models, models_dev, previous)
    provider = payload["opencode-go"]

    # The list is exactly go.mdx ids: the models.dev-only id is never added.
    assert set(provider["models"]) == {"grok-4.6", "sample-bot"}
    assert enriched == 1
    # The provider envelope survives from the previous snapshot.
    assert provider["id"] == "opencode-go"
    assert provider["api"] == "https://example.test"

    card = provider["models"]["grok-4.6"]
    assert card["description"] == "upstream card"
    assert card["cost"] == {"input": 0.2}
    assert card["limit"] == {"context": 128000}
    # go.mdx owns the quota fields, mirrored under extra in goat shape.
    assert card["rp5h"] == 169
    assert card["usage_quota"] == 20
    assert card["extra"] == {"rp5h": 169, "usage_quota": 20}

    # go-exclusive id: go.mdx-claimed fields only, no invented card data.
    exclusive = provider["models"]["sample-bot"]
    assert exclusive["name"] == "Sample Bot"
    assert "description" not in exclusive
    assert "cost" not in exclusive
    assert exclusive["extra"] == {"rp5h": 1000, "usage_quota": 60}


def test_build_snapshot_without_previous_creates_envelope() -> None:
    go_models = {"solo": {"model_id": "solo", "name": "Solo", "rp5h": None, "usage_quota": None}}

    payload, enriched = build_opencode_go_snapshot(go_models, None, None)

    provider = payload["opencode-go"]
    assert provider["id"] == "opencode-go"
    assert provider["models"]["solo"]["rp5h"] is None
    assert enriched == 0


def test_load_models_dev_provider_accepts_three_shapes(tmp_path: Path) -> None:
    provider_node = {"models": {"a": {"id": "a"}}}

    full = tmp_path / "api.json"
    full.write_text(json.dumps({"opencode-go": provider_node, "other": {}}), encoding="utf-8")
    extract = tmp_path / "extract.json"
    extract.write_text(json.dumps({"opencode-go": provider_node}), encoding="utf-8")
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(provider_node), encoding="utf-8")

    assert load_models_dev_provider(None) == {}
    for path in (full, extract, bare):
        assert load_models_dev_provider(path) == {"a": {"id": "a"}}

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"unrelated": True}), encoding="utf-8")
    try:
        load_models_dev_provider(broken)
    except ValueError as exc:
        assert "opencode-go provider" in str(exc)
    else:
        raise AssertionError("expected ValueError for shapeless models.dev source")


def test_write_json_atomic_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    payload = {"a": 1}
    write_json_atomic(path, payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
