#!/usr/bin/env python3
"""Tests for the RP5H channel-selection script (select.py)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ``select`` shadows a stdlib module name, so load it by path under a
# namespace that cannot collide with sys.modules["select"].
_SCRIPT = Path(__file__).parent.parent / "scripts" / "select.py"
_spec = importlib.util.spec_from_file_location("model_select", _SCRIPT)
select_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(select_module)

load_channel_snapshot = select_module.load_channel_snapshot
main = select_module.main
record_rp5h = select_module.record_rp5h
select_channels = select_module.select_channels


def envelope(channel: str, models: dict) -> dict:
    return {channel: {"id": channel, "models": {k: {"id": k, **v} for k, v in models.items()}}}


def opencode_snapshot(models: dict) -> dict:
    return envelope("opencode-go", models)


def goat_snapshot(models: dict) -> dict:
    return envelope("commandcode-goat", models)


def test_record_rp5h_reads_extra_then_top_level():
    assert record_rp5h({"extra": {"rp5h": 18200}}) == 18200.0
    assert record_rp5h({"rp5h": 500}) == 500.0
    assert record_rp5h({"extra": {"rp5h": None}}) is None
    assert record_rp5h({}) is None
    assert record_rp5h({"rp5h": "n/a"}) is None


def test_select_prefers_higher_rp5h_channel():
    snapshots = [
        ("opencode-go", {"deepseek-v4-flash": {"extra": {"rp5h": 7600}}}),
        ("commandcode-goat", {"deepseek-v4-flash": {"extra": {"rp5h": 18200}}}),
    ]

    selection = select_channels(snapshots)

    entry = selection["deepseek-v4-flash"]
    assert entry["channel"] == "commandcode-goat"
    assert entry["rp5h"] == 18200.0
    assert entry["candidates"] == {
        "commandcode-goat": {"rp5h": 18200.0},
        "opencode-go": {"rp5h": 7600.0},
    }


def test_select_tie_breaks_by_first_source():
    snapshots = [
        ("opencode-go", {"shared": {"extra": {"rp5h": 100}}}),
        ("commandcode-goat", {"shared": {"extra": {"rp5h": 100}}}),
    ]

    selection = select_channels(snapshots)

    assert selection["shared"]["channel"] == "opencode-go"


def test_select_missing_rp5h_loses_to_present_value():
    snapshots = [
        ("opencode-go", {"model": {}}),
        ("commandcode-goat", {"model": {"extra": {"rp5h": 42}}}),
    ]

    selection = select_channels(snapshots)

    assert selection["model"]["channel"] == "commandcode-goat"
    assert selection["model"]["rp5h"] == 42.0


def test_select_union_keeps_single_channel_models():
    snapshots = [
        ("opencode-go", {"only-go": {"extra": {"rp5h": 10}}}),
        ("commandcode-goat", {"only-goat": {"extra": {"rp5h": 20}}}),
    ]

    selection = select_channels(snapshots)

    assert set(selection) == {"only-go", "only-goat"}
    assert selection["only-go"]["channel"] == "opencode-go"
    assert selection["only-goat"]["channel"] == "commandcode-goat"
    assert selection["only-go"]["rp5h"] == 10.0


def test_select_all_missing_rp5h_yields_null_channel():
    snapshots = [
        ("opencode-go", {"ghost": {}}),
        ("commandcode-goat", {"ghost": {}}),
    ]

    selection = select_channels(snapshots)

    assert selection["ghost"]["channel"] is None
    assert selection["ghost"]["rp5h"] is None


def test_select_single_channel_without_rp5h_keeps_channel():
    snapshots = [("commandcode-goat", {"solo": {}})]

    selection = select_channels(snapshots)

    assert selection["solo"]["channel"] == "commandcode-goat"
    assert selection["solo"]["rp5h"] is None


def test_load_channel_snapshot_rejects_bad_envelope(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")

    with pytest.raises(ValueError, match="provider envelope"):
        load_channel_snapshot(bad)


def test_main_writes_selection_and_rejects_duplicate_channels(tmp_path):
    first = tmp_path / "go.json"
    second = tmp_path / "goat.json"
    first.write_text(json.dumps(opencode_snapshot({"m1": {"extra": {"rp5h": 100}}})), encoding="utf-8")
    second.write_text(
        json.dumps(goat_snapshot({"m1": {"extra": {"rp5h": 200}}, "m2": {}})),
        encoding="utf-8",
    )
    output = tmp_path / "model_select.json"

    assert (
        main(["--source", str(first), "--source", str(second), "--output", str(output)])
        == 0
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["models"]["m1"]["channel"] == "commandcode-goat"
    assert document["models"]["m2"]["channel"] == "commandcode-goat"
    assert not list(tmp_path.glob(".model_select.json.*.tmp"))

    assert (
        main(["--source", str(first), "--source", str(first), "--output", str(output)])
        == 1
    )


def test_main_rejects_unrecognized_snapshot(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"unexpected": {"x": 1}}), encoding="utf-8")

    assert main(["--source", str(bad), "--output", str(tmp_path / "out.json")]) == 1
