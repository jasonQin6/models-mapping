#!/usr/bin/env python3
"""Tests for the OpenCode Go watcher (updates models_extra.json from go.mdx)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from models_extra import ExtraStoreError, is_excluded_model, load_document, update_channel
from watch_go import build_go_section, main

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_content() -> str:
    return (FIXTURES / "go-sample.mdx").read_text(encoding="utf-8")


def test_build_go_section_fixture_yields_channel_facts() -> None:
    models = build_go_section(_fixture_content())

    # go.mdx owns the list: exactly its ids, the channel claude model dropped.
    assert set(models) == {"grok-4.6", "sample-bot", "freebie"}
    grok = models["grok-4.6"]
    assert grok["name"] == "Grok 4.6"
    assert grok["rp5h"] == 169
    assert grok["usage_quota"] == 20
    # The four mdx price columns assemble into cost with models.dev key names.
    assert grok["cost"] == {
        "input": 0.2,
        "output": 0.8,
        "cache_read": 0.02,
        "cache_write": 0.1,
    }
    assert grok["context_threshold"] is None
    assert grok["peak_hours"] is None

    bot = models["sample-bot"]
    assert bot["rp5h"] == 1000
    assert bot["usage_quota"] == 60
    assert bot["cost"]["cache_write"] == 0.375


def test_free_model_keeps_zero_prices_and_parser_quota_backfill() -> None:
    models = build_go_section(_fixture_content())

    freebie = models["freebie"]
    # parse_mdx backfills blank free quotas from the largest non-free values;
    # planning re-derives them against the model's owning channel.
    assert freebie["rp5h"] == 1000
    assert freebie["usage_quota"] == 60
    assert freebie["cost"] == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def test_channel_claude_models_are_not_collected() -> None:
    models = build_go_section(_fixture_content())

    assert "claude-sonnet-5" not in models
    assert is_excluded_model("claude-sonnet-5")
    assert is_excluded_model("Claude-Opus-4")
    assert not is_excluded_model("grok-4.6")


def test_update_channel_merges_into_shared_store(tmp_path: Path) -> None:
    path = tmp_path / "models_extra.json"

    update_channel(path, "opencode-go", {"grok-4.6": {"rp5h": 169}})
    update_channel(path, "commandcode-goat", {"deepseek-v4-flash": {"rp5h": 18200}})

    document = load_document(path)
    assert document["schema_version"] == 1
    assert set(document["channels"]) == {"opencode-go", "commandcode-goat"}
    assert document["channels"]["opencode-go"] == {"grok-4.6": {"rp5h": 169}}
    assert document["updated_at"]


def test_update_channel_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "models_extra.json"
    path.write_text(json.dumps({"schema_version": 99, "channels": {}}), encoding="utf-8")

    with pytest.raises(ExtraStoreError):
        update_channel(path, "opencode-go", {})


def test_main_writes_section_from_local_fixture(tmp_path: Path) -> None:
    out = tmp_path / "models_extra.json"

    rc = main(["--extra", str(out), str(FIXTURES / "go-sample.mdx")])

    assert rc == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert set(document["channels"]["opencode-go"]) == {"grok-4.6", "sample-bot", "freebie"}
