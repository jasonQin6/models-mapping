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
    assert set(models) == {"grok-4.6", "sample-bot", "freebie", "union-alpha"}
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

    bot = models["sample-bot"]
    assert bot["rp5h"] == 1000
    assert bot["usage_quota"] == 60
    assert bot["cost"]["cache_write"] == 0.375


def test_free_worded_pricing_row_transcribes_zero_prices() -> None:
    models = build_go_section(_fixture_content())

    # The real-world Union Alpha Free shape: the id cell has no free marker
    # and the pricing row says ``Free`` in words.  Collection transcribes
    # the declaration as zero prices — nothing else; freeness itself is a
    # compute-layer determination from the declared cost.
    alpha = models["union-alpha"]
    assert alpha["name"] == "Union Alpha Free"
    assert "free" not in alpha
    assert alpha["rp5h"] is None
    assert alpha["usage_quota"] is None
    assert alpha["cost"] == {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": None,
    }


def test_priced_rows_transcribe_their_numbers() -> None:
    models = build_go_section(_fixture_content())

    # A plain priced row and a ``$0`` row are numbers, not Free wording:
    # both transcribe as-is and carry no freeness signal here.
    assert models["grok-4.6"]["cost"]["output"] == 0.8
    assert models["freebie"]["cost"] == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    assert all("free" not in m for m in models.values())


def test_free_model_transcribes_declarations_without_backfill() -> None:
    models = build_go_section(_fixture_content())

    freebie = models["freebie"]
    # Collection transcribes declarations only: the usage row is "-" and the
    # pricing row declares $0.  Deriving free quotas is the planner's job.
    assert freebie["rp5h"] is None
    assert freebie["usage_quota"] is None
    assert freebie["cost"] == {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def test_channel_claude_models_are_not_collected() -> None:
    models = build_go_section(_fixture_content())

    assert "claude-sonnet-5" not in models
    assert is_excluded_model("claude-sonnet-5")
    assert is_excluded_model("Claude-Opus-4")
    assert not is_excluded_model("grok-4.6")


def test_missing_requests_table_is_structural_drift() -> None:
    # Upstream reshuffles must fail the run, never silently null rp5h.
    drifted = _fixture_content().replace("requests per 5 hour", "requests hourly")

    with pytest.raises(ValueError, match="requests table"):
        build_go_section(drifted)


def test_missing_pricing_table_is_structural_drift() -> None:
    drifted = _fixture_content().replace("Input", "Rate").replace("Output", "Price")

    with pytest.raises(ValueError, match="pricing table"):
        build_go_section(drifted)


def test_pricing_without_priced_rows_is_structural_drift() -> None:
    drifted = (
        _fixture_content()
        .replace("$0.80", "incl")
        .replace("$1.20", "incl")
        .replace("$2.40", "incl")
        .replace("$15.00", "incl")
        .replace("$0", "incl")
        .replace("| Free   | Free   | Free        |", "| incl    | incl    | incl        |")
    )

    with pytest.raises(ValueError, match="no priced rows"):
        build_go_section(drifted)


def test_update_channel_merges_into_shared_store(tmp_path: Path) -> None:
    path = tmp_path / "models_extra.json"

    update_channel(path, "opencode-go", {"grok-4.6": {"rp5h": 169}})
    update_channel(path, "commandcode-goat", {"deepseek-v4-flash": {"rp5h": 18200}})

    document = load_document(path)
    assert document["schema_version"] == 1
    assert set(document["channels"]) == {"opencode-go", "commandcode-goat"}
    assert document["channels"]["opencode-go"] == {"grok-4.6": {"rp5h": 169}}
    assert document["updated_at"]


def test_update_channel_preserves_hand_maintained_aliases(tmp_path: Path) -> None:
    path = tmp_path / "models_extra.json"
    update_channel(path, "opencode-go", {"grok-4.6": {"rp5h": 169}})
    document = load_document(path)
    document["aliases"] = {"tencent-hy3": "hy3"}
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    update_channel(path, "commandcode-goat", {"deepseek-v4-flash": {"rp5h": 18200}})

    reloaded = load_document(path)
    assert reloaded["aliases"] == {"tencent-hy3": "hy3"}
    assert set(reloaded["channels"]) == {"opencode-go", "commandcode-goat"}


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
    assert set(document["channels"]["opencode-go"]) == {
        "grok-4.6",
        "sample-bot",
        "freebie",
        "union-alpha",
    }


def test_update_channel_free_rescission_is_an_ordinary_cost_refresh(tmp_path: Path) -> None:
    path = tmp_path / "models_extra.json"
    update_channel(path, "opencode-go", {"union-alpha": {"cost": {"input": 0, "output": 0}}})

    # Upstream charging again is just a contract-field refresh: no flag
    # machinery exists on either side.
    update_channel(
        path, "opencode-go", {"union-alpha": {"cost": {"input": 0.2, "output": 0.8}}}
    )

    document = load_document(path)
    assert document["channels"]["opencode-go"]["union-alpha"]["cost"] == {
        "input": 0.2,
        "output": 0.8,
    }


def test_update_channel_preserves_hand_maintained_record_fields(tmp_path: Path) -> None:
    path = tmp_path / "models_extra.json"
    update_channel(path, "commandcode-goat", {"laguna-s-2.1-free": {"rp5h": None, "note": "hand"}})

    # A scraper never touches keys it does not produce: the goat section's
    # refresh refreshes contract fields and leaves hand fields exactly as
    # maintained.
    update_channel(path, "commandcode-goat", {"laguna-s-2.1-free": {"rp5h": 900}})

    document = load_document(path)
    assert document["channels"]["commandcode-goat"]["laguna-s-2.1-free"] == {
        "rp5h": 900,
        "note": "hand",
    }
