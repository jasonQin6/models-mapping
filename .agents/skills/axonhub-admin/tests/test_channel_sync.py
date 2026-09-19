#!/usr/bin/env python3
"""Tests for the channel-sync step: blocklist materialization + allow regex."""

import json
import re
from pathlib import Path

from channel_sync import main, materialize_blocklist, sync_pattern  # noqa: E402
from testutil import arena_doc, arena_models, rec  # noqa: E402


def test_sync_pattern_enumerates_blocklist_with_fixed_claude_block() -> None:
    pattern = sync_pattern(["zai-org/GLM-5.2-fast", "minimax-m3"])

    # (?i) prefix, vendor prefixes stripped, metacharacters escaped, ids terminated.
    # Original spelling is kept verbatim; case-insensitivity comes from (?i).
    assert pattern == (
        r"(?i)^(?!.*(^|/)claude-)"
        r"(?!.*(^|/)(?:GLM\-5\.2\-fast|minimax\-m3)(?:$|[:/])).*$"
    )


def test_sync_pattern_without_entries_is_claude_block_only() -> None:
    assert sync_pattern([]) == r"(?i)^(?!.*(^|/)claude-).*$"


def test_sync_pattern_dedupes_bare_forms() -> None:
    pattern = sync_pattern(["zai-org/glm-5", "glm-5"])

    assert pattern.count("glm") == 1



def test_sync_pattern_blocks_and_allows_by_id(tmp_path: Path) -> None:
    pattern = sync_pattern(["minimax-m3", "deepseek-v4-flash-fast"])

    blocked = [
        "minimax-m3",
        "MiniMaxAI/MiniMax-M3",        # mixed case with vendor prefix
        "minimax-m3:beta",              # colon-suffixed variant
        "anthropic/claude-sonnet-4-5",  # Claude served by AxonHub global models
        "claude-opus-5",
    ]
    allowed = [
        "minimax-m3-ultra",             # termination required: longer id passes
        "zai-org/GLM-5.3",
        "someclaude-x",                 # claude must sit at a segment start
    ]
    for value in blocked:
        assert re.match(pattern, value) is None, value
    for value in allowed:
        assert re.match(pattern, value) is not None, value


def test_materialize_blocklist_preserves_human_and_regenerates_derived() -> None:
    # Human reasons (manual/tier/retired/…) pass through verbatim and win
    # over derived entries for the same id; planner-owned reasons are
    # rebuilt from the current snapshots (stale ones disappear).
    raw = {
        "commandcode-goat": [
            {"id": "kimi-k2.5", "reason": "tier"},
            {"id": "legacy-fast", "reason": "speed: fast/highspeed suffix"},  # stale: id gone
        ],
    }
    sections = {
        "commandcode-goat": {
            "kimi-k2.5": rec(rp5h=900),                       # human entry kept
            "glm-5.2-fast": rec(rp5h=138),                    # speed derived
            "minimax-m3": rec(rp5h=3200),                     # arena 1487 -> lowscore derived
        },
    }
    arena = {"minimax-m3": 1487.3}

    merged = materialize_blocklist(raw, sections, {}, arena_models(arena))

    entries = {e["id"]: e["reason"] for e in merged["commandcode-goat"]}
    assert entries["kimi-k2.5"] == "tier"
    assert "legacy-fast" not in entries  # stale derived entry regenerated away
    assert entries["glm-5.2-fast"].startswith("speed:")
    assert entries["minimax-m3"].startswith("lowscore:")


def test_materialize_blocklist_human_wins_over_derived() -> None:
    # A human exclude for an id that would also derive lowscore keeps the
    # human reason.
    raw = {"commandcode-goat": [{"id": "minimax-m3", "reason": "manual"}]}
    sections = {"commandcode-goat": {"minimax-m3": rec(rp5h=3200)}}
    arena = {"minimax-m3": 1487.3}

    merged = materialize_blocklist(raw, sections, {}, arena_models(arena))

    assert merged["commandcode-goat"] == [{"id": "minimax-m3", "reason": "manual"}]


def test_materialize_blocklist_supersedes_lost_variants() -> None:
    # A losing variant a channel still serves is blocked; the winner is not.
    # Grouping is global: opencode-go's plain longcat-2.0 loses to the -free
    # variant served by another channel.
    sections = {
        "commandcode-goat": {
            "muse-spark-1.2": rec(rp5h=11400),
            "muse-spark-1.2-contributor": rec(rp5h=45300),
            "longcat-2.0-free": rec(rp5h=None),
        },
        "opencode-go": {
            "longcat-2.0": rec(rp5h=11400),
        },
    }

    merged = materialize_blocklist({}, sections, {}, arena_models({}))

    goat = {e["id"]: e["reason"] for e in merged["commandcode-goat"]}
    open_go = {e["id"]: e["reason"] for e in merged["opencode-go"]}
    assert goat["muse-spark-1.2"] == "superseded: replaced by muse-spark-1.2-contributor"
    assert "muse-spark-1.2-contributor" not in goat  # survivor never blocked
    assert open_go["longcat-2.0"] == "superseded: replaced by longcat-2.0-free"
    assert "longcat-2.0-free" not in open_go


def test_materialize_blocklist_superseded_yields_to_human_and_triage() -> None:
    # A human ruling keeps its reason; a plain id that never competes in a
    # variant group derives nothing.
    raw = {"commandcode-goat": [{"id": "muse-spark-1.2", "reason": "retired: keep human"}]}
    sections = {
        "commandcode-goat": {
            "muse-spark-1.2": rec(rp5h=11400),
            "muse-spark-1.2-contributor": rec(rp5h=45300),
            "glm-5.3": rec(rp5h=220),  # -flash is not a variant suffix: no group
        },
    }

    merged = materialize_blocklist(raw, sections, {}, arena_models({}))

    goat = {e["id"]: e["reason"] for e in merged["commandcode-goat"]}
    assert goat["muse-spark-1.2"] == "retired: keep human"
    assert "glm-5.3" not in goat


def test_main_materializes_and_prints_patterns(tmp_path: Path, capsys) -> None:
    # The single channel-sync entry point: rebuilds the derived classes in
    # the stored blocklist and prints the per-channel allow regex as JSON.
    # The collection store is read-only here; the blocklist file is the
    # only thing this step writes.
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "aliases": {"x": "y"},
        "channels": {"commandcode-goat": {
            "glm-5.2-fast": rec(rp5h=138),
            "kimi-k2.5": rec(rp5h=900),
            "minimax-m3": rec(rp5h=3200),
        }},
    }), encoding="utf-8")
    blocklist = tmp_path / "blocklist.json"
    blocklist.write_text(json.dumps({
        "schema_version": 1,
        "blocklist": {"commandcode-goat": [{"id": "kimi-k2.5", "reason": "tier"}]},
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps(arena_doc({"minimax-m3": 1487.3})), encoding="utf-8")
    extra_before = extra.read_text(encoding="utf-8")

    rc = main(["--extra", str(extra), "--arena", str(arena), "--blocklist", str(blocklist)])

    assert rc == 0
    doc = json.loads(blocklist.read_text(encoding="utf-8"))
    entries = {e["id"]: e["reason"] for e in doc["blocklist"]["commandcode-goat"]}
    assert entries["kimi-k2.5"] == "tier"                       # human preserved
    assert entries["glm-5.2-fast"].startswith("speed:")         # derived rebuilt
    assert entries["minimax-m3"].startswith("lowscore:")
    assert extra.read_text(encoding="utf-8") == extra_before    # collection store untouched
    patterns = json.loads(capsys.readouterr().out)
    assert set(patterns) == {"commandcode-goat"}
    assert patterns["commandcode-goat"] == sync_pattern(
        [entry["id"] for entry in doc["blocklist"]["commandcode-goat"]]
    )
    # Every stored entry, human and derived alike, enumerates into the regex.
    assert "kimi\\-k2\\.5" in patterns["commandcode-goat"]


def test_main_channel_filter_and_unknown(tmp_path: Path, capsys) -> None:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "channels": {"commandcode-goat": {"glm-5.2-fast": rec(rp5h=138)}},
    }), encoding="utf-8")
    blocklist = tmp_path / "blocklist.json"
    blocklist.write_text(json.dumps({
        "schema_version": 1,
        "blocklist": {
            "commandcode-goat": [{"id": "glm-5.2-fast", "reason": "speed: fast/highspeed suffix"}],
            "opencode-go": [{"id": "kimi-k2.5", "reason": "tier"}],
        },
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps(arena_doc({})), encoding="utf-8")

    rc = main(["--extra", str(extra), "--arena", str(arena), "--blocklist", str(blocklist),
               "--channel", "opencode-go", "--channel", "missing"])

    assert rc == 0
    captured = capsys.readouterr()
    patterns = json.loads(captured.out)
    assert set(patterns) == {"opencode-go"}
    assert "no blocklist section for channel 'missing'" in captured.err


def test_main_reports_section_missing_human_entries(tmp_path: Path, capsys) -> None:
    # A human entry whose id has left the channel's section is reported on
    # stderr: the derived classes heal themselves, human entries go stale.
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "aliases": {},
        "channels": {"opencode-go": {"qwen3.8-flash": {"name": "Q", "cost": {}}}},
    }, ensure_ascii=False))
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps({"schema_version": 1, "models": {}}))
    blocklist = tmp_path / "blocklist.json"
    blocklist.write_text(json.dumps({
        "schema_version": 1,
        "updated_at": "2026-09-18T00:00:00Z",
        "blocklist": {"opencode-go": [
            {"id": "omen-alpha", "reason": "retired"},
            {"id": "qwen3.8-flash", "reason": "tier"},
        ]},
    }, ensure_ascii=False))

    exit_code = main(["--extra", str(extra), "--arena", str(arena), "--blocklist", str(blocklist)])

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "omen-alpha" in err
    # qwen3.8-flash is still a section key: alive, not reported.
    assert "tier" not in err.split("人工条目已不在节键")[-1]
