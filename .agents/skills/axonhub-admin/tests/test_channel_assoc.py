#!/usr/bin/env python3
"""Tests for the non-Claude association step: priority chains + drafts."""

import json
from pathlib import Path

from channel_assoc import build_associations, intersection_count, main  # noqa: E402
from testutil import build, rec  # noqa: E402


def test_channel_priority_orders_by_rp5h_and_counts_intersection() -> None:
    # Two collected channels declare the same canonical (via the alias map):
    # the higher rp5h is p0, the null-rp5h static section trails last.
    sections = {
        "commandcode-goat": {"tencent-hy3": rec(rp5h=7080)},
        "opencode-go": {"hy3": rec(rp5h=4300)},
        "ant": {"hy3": rec(rp5h=None)},
        "sensenova": {"solo": rec(rp5h=100)},
    }

    result = build(sections, aliases={"tencent-hy3": "hy3"})
    plans = {plan["modelID"]: plan for plan in build_associations(result)}

    assert [step["channel"] for step in plans["hy3"]["channelPriority"]] == [
        "commandcode-goat", "opencode-go", "ant",
    ]
    assert [step["priority"] for step in plans["hy3"]["channelPriority"]] == [0, 1, 2]
    assert plans["hy3"]["channelPriority"][0]["rp5h"] == 7080.0
    assert plans["hy3"]["channelPriority"][2]["rp5h"] is None
    # Single-channel models degrade to a p0 pin.
    assert [step["channel"] for step in plans["solo"]["channelPriority"]] == ["sensenova"]
    assert intersection_count(result) == 1


def test_alias_case_keeps_native_spellings_in_channel_aliases() -> None:
    # The alias case records goat's tencent-hy3 native spelling so the
    # session pins associations to what each channel actually exposes
    # (the canonical hy3 for opencode-go, tencent-hy3 for the goat channel).
    sections = {
        "commandcode-goat": {"tencent-hy3": rec(rp5h=7080)},
        "opencode-go": {"hy3": rec(rp5h=4300)},
    }

    result = build(sections, aliases={"tencent-hy3": "hy3"})
    plans = {plan["modelID"]: plan for plan in build_associations(result)}

    assert plans["hy3"]["channelAliases"] == {"commandcode-goat": "tencent-hy3"}
    assert [step["channel"] for step in plans["hy3"]["channelPriority"]] == [
        "commandcode-goat", "opencode-go",
    ]


def test_free_flag_carries_into_the_plan() -> None:
    sections = {"commandcode-goat": {"longcat-2.0-free": rec(rp5h=None, cost={"input": 0, "output": 0})}}

    result = build(sections)
    plans = {plan["modelID"]: plan for plan in build_associations(result)}

    assert plans["longcat-2.0-free"]["free"] is True


def test_main_prints_plans_and_id_filter(tmp_path: Path, capsys) -> None:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "channels": {"commandcode-goat": {
            "tencent-hy3": rec(rp5h=7080),
            "solo": rec(rp5h=100),
        }},
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps({"schema_version": 1, "models": {}}), encoding="utf-8")
    blocklist = tmp_path / "blocklist.json"
    blocklist.write_text(json.dumps({"schema_version": 1, "blocklist": {}}), encoding="utf-8")

    argv = ["--extra", str(extra), "--arena", str(arena), "--blocklist", str(blocklist)]
    assert main(argv) == 0
    plans = json.loads(capsys.readouterr().out)
    assert {plan["modelID"] for plan in plans} == {"tencent-hy3", "solo"}

    assert main([*argv, "--id", "solo"]) == 0
    plans = json.loads(capsys.readouterr().out)
    assert [plan["modelID"] for plan in plans] == ["solo"]

    assert main([*argv, "--id", "missing"]) == 1
